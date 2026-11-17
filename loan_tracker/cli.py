"""Command line: python -m loan_tracker.cli <command>

  migrate                    create / upgrade the database tables
  create-user USERNAME       set the owner login (asks for a password, prints
                             the authenticator-app setup key); re-run to reset
  run-job [--date YYYY-MM-DD] [--dry-run]
                             the daily job (cron); --dry-run prints the digest
  demo-data                  load the worked examples A-C (for trying the app)
"""
import argparse
import getpass
import sys
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from loan_tracker import auth, services as svc
from loan_tracker.config import get_settings, today
from loan_tracker.db import models as m
from loan_tracker.db.migrate import upgrade
from loan_tracker.db.session import session_scope


def cmd_create_user(args) -> None:
    password = args.password or getpass.getpass("New password (min 10 characters): ")
    if not args.password and password != getpass.getpass("Repeat password: "):
        sys.exit("passwords do not match")
    secret = None if args.no_2fa else auth.new_totp_secret()
    with session_scope() as s:
        u = s.scalar(select(m.AppUser).where(m.AppUser.username == args.username))
        if u is None:
            u = m.AppUser(username=args.username, password_hash="")
            s.add(u)
        u.password_hash = auth.hash_password(password)
        u.totp_secret = secret
    print(f"User '{args.username}' saved.")
    if secret:
        print("\nAdd this account to your authenticator app (Google Authenticator, Aegis, ...):")
        print(f"  Setup key : {secret}")
        print(f"  Or URI    : {auth.otpauth_uri(secret, args.username)}")
        print("You will need the 6-digit code at every login.")


def cmd_run_job(args) -> None:
    from loan_tracker.jobs.daily import build_digest, run_daily
    from loan_tracker.jobs.notify import default_senders
    settings = get_settings()
    run_date = date.fromisoformat(args.date) if args.date else today(settings)
    if args.dry_run:
        with session_scope() as s:
            print(build_digest(s, run_date, settings).text())
            s.rollback()
        return
    try:
        with session_scope() as s:
            run = run_daily(s, run_date, settings, default_senders(settings))
            print(f"{run_date}: {'ok' if run.ok else 'FAILED'} - {run.summary}")
    except Exception as exc:
        with session_scope() as s:  # record the failure so the dashboard shows it
            s.add(m.JobRun(run_date=run_date, ok=False, finished_at=datetime.now(timezone.utc),
                           summary=f"error: {exc!r}"[:1000]))
        raise
    if not run.ok:
        sys.exit(1)


def cmd_demo_data(args) -> None:
    """Examples A-C from docs/05-examples.md, dated 2026."""
    t = today()
    with session_scope() as s:
        ravi = svc.create_party(s, "Ravi (demo)", phone="98480 00001")
        suresh = svc.create_party(s, "Suresh (demo)", phone="98480 00002")
        relative = svc.create_party(s, "Relative (demo)")
        a = svc.create_loan(s, party_id=ravi.id, direction="GIVEN", book="PERSONAL", principal_paise=10_000_000,
                            start_date=date(2026, 1, 1), rate=Decimal(2), due_day=1, as_of=t)
        svc.post_entry(s, a.id, type="PAYMENT", entry_date=date(2026, 2, 5), amount_paise=150_000, as_of=t)
        svc.post_entry(s, a.id, type="PAYMENT", entry_date=date(2026, 3, 15), amount_paise=3_250_000, as_of=t)
        taken = svc.create_loan(s, party_id=suresh.id, direction="TAKEN", book="BUSINESS",
                                principal_paise=10_000_000, start_date=date(2026, 1, 1),
                                rate=Decimal("1.5"), due_day=5, as_of=t)
        given = svc.create_loan(s, party_id=ravi.id, direction="GIVEN", book="BUSINESS",
                                principal_paise=10_000_000, start_date=date(2026, 1, 1),
                                rate=Decimal("2.5"), due_day=10, as_of=t)
        svc.link_funding(s, taken_loan_id=taken.id, given_loan_id=given.id, amount_paise=10_000_000,
                         link_date=date(2026, 1, 1))
        rel = svc.create_loan(s, party_id=relative.id, direction="TAKEN", book="PERSONAL",
                              principal_paise=5_000_000, start_date=date(2026, 1, 1), rate=Decimal(1),
                              as_of=t)
        mahesh = svc.create_party(s, "Mahesh (demo)")
        biz = svc.create_loan(s, party_id=mahesh.id, direction="GIVEN", book="BUSINESS",
                              principal_paise=5_000_000, start_date=date(2026, 1, 1), rate=Decimal(2),
                              as_of=t)
        svc.link_funding(s, taken_loan_id=rel.id, given_loan_id=biz.id, amount_paise=5_000_000,
                         link_date=date(2026, 1, 1), confirm_cross_book=True)
    print("Demo loans created (names end in '(demo)').")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m loan_tracker.cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    u = sub.add_parser("create-user")
    u.add_argument("username")
    u.add_argument("--password", help=argparse.SUPPRESS)
    u.add_argument("--no-2fa", action="store_true", help="skip the authenticator code (not recommended)")
    j = sub.add_parser("run-job")
    j.add_argument("--date")
    j.add_argument("--dry-run", action="store_true")
    sub.add_parser("demo-data")
    args = p.parse_args(argv)
    if args.cmd == "migrate":
        upgrade()
        print("database is up to date")
    elif args.cmd == "create-user":
        cmd_create_user(args)
    elif args.cmd == "run-job":
        cmd_run_job(args)
    elif args.cmd == "demo-data":
        cmd_demo_data(args)


if __name__ == "__main__":
    main()
