"""JSON for the React app: one payload per screen, plus the actions that used to be HTML-only.

Every number comes from the same services/reports the server-rendered pages used, so the
React screens show exactly what the old pages showed. The browser never calculates money:
it formats the paise sent here and posts amounts as typed text, parsed by the server.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from loan_tracker import auth, reports, services as svc
from loan_tracker.db import models as m
from loan_tracker.engine.calendar import _add_months as add_months
from loan_tracker.web import formatting as fmt

# columns never sent to the browser
SECRET_COLUMNS = {"password_hash", "totp_secret", "token_hash", "uuid", "key_id", "sha256", "fingerprint"}
CLOSED = ("CLOSED", "SETTLED", "WRITTEN_OFF")


def J(obj: Any) -> Any:
    """JSON-safe copy: dataclasses and ORM rows (columns only, secrets dropped), dates as ISO
    text, Decimals as text. A loan or document also carries its person's name."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return f"{obj.normalize():f}"          # 1.5000 -> "1.5", 2.0000 -> "2"
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: J(getattr(obj, f.name)) for f in fields(obj)}
    if hasattr(obj, "__table__"):
        out = {c.key: J(getattr(obj, c.key)) for c in obj.__table__.columns if c.key not in SECRET_COLUMNS}
        if isinstance(obj, (m.Loan, m.Document)) and getattr(obj, "party", None) is not None:
            out["party_name"] = obj.party.name
        return out
    if isinstance(obj, dict):
        return {str(k): J(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [J(v) for v in obj]
    return str(obj)


def loan_brief(row: svc.LoanRow) -> dict:
    """One line of a loan list."""
    loan, res = row.loan, row.result
    b = res.balances
    late = [d for d in res.dues if d.status == "OVERDUE"]
    nxt = res.projected_due
    return {
        "id": loan.id, "party_id": loan.party_id, "party_name": loan.party.name, "phone": loan.party.phone,
        "direction": loan.direction, "book": loan.book, "status": row.status,
        "principal_paise": loan.principal_paise, "rate": J(loan.rate), "rate_unit": loan.rate_unit,
        "interest_cycle": loan.interest_cycle, "start_date": J(loan.start_date), "due_day": loan.due_day,
        "maturity_date": J(loan.maturity_date),
        "principal_outstanding_paise": b.principal_outstanding_paise,
        "interest_outstanding_paise": b.interest_outstanding_paise,
        "penalty_outstanding_paise": b.penalty_outstanding_paise,
        "payoff_today_paise": b.payoff_today_paise, "credit_paise": b.credit_paise,
        "overdue_paise": sum(d.outstanding_paise for d in late),
        "overdue_days": max((d.overdue_days for d in late), default=0),
        "overdue_count": len(late),
        "next_due_date": J(nxt.due_date) if nxt else None,
        "next_due_paise": nxt.interest_paise if nxt else 0,
    }


# ------------------------------------------------------------------ request bodies
class LoginIn(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=500)
    code: str = Field("", max_length=12)


class DeviceLoginIn(LoginIn):
    device_name: str = Field("Phone", max_length=100)


class PartyUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field("", max_length=40)
    address: str = Field("", max_length=500)
    id_proof_ref: str = Field("", max_length=200)
    guarantor: str = Field("", max_length=200)
    notes: str = Field("", max_length=2000)
    risk_tag: str = Field("", max_length=40)


class StatusIn(BaseModel):
    status: str = Field("", max_length=20)


class ShareIn(BaseModel):
    days: int = 30


class ActiveIn(BaseModel):
    active: bool


class BackfillIn(BaseModel):
    loan_id: int | None = None


class FormLoanIn(BaseModel):
    """The new-loan form as typed: amounts and rates are text the server parses."""
    party_id: int
    direction: str
    book: str = ""
    principal: str = Field(max_length=30)
    start_date: str = ""
    rate: str = Field(max_length=20)
    rate_unit: str = "PER_MONTH"
    interest_cycle: str = "MONTHLY"
    due_day: str = ""
    grace_days: int = 3
    maturity_date: str = ""
    purpose: str = Field("", max_length=500)
    collateral: str = Field("", max_length=500)
    payment_mode: str = "CASH"


class FormEntryIn(BaseModel):
    type: str
    entry_date: str = ""
    amount: str = Field("", max_length=30)
    new_rate: str = Field("", max_length=20)
    payment_mode: str = "CASH"
    reference: str = Field("", max_length=200)
    note: str = Field("", max_length=1000)


class FormContactIn(BaseModel):
    kind: str = "CALL"
    outcome: str = "REACHED"
    contact_date: str = ""
    note: str = Field("", max_length=2000)
    next_follow_up: str = ""
    promise_amount: str = Field("", max_length=30)
    promise_date: str = ""


class FormFundingIn(BaseModel):
    taken_loan_id: int
    given_loan_id: int
    amount: str = Field(max_length=30)
    link_date: str = ""
    confirm_cross_book: bool = False


class FormCashAccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = "BANK"
    book: str = "BUSINESS"
    balance: str = Field("0", max_length=30)


class FormBalanceIn(BaseModel):
    balance: str = Field(max_length=30)
    on: str = ""
    note: str = Field("", max_length=500)


class FormPlannedIn(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    amount: str = Field(max_length=30)
    item_date: str
    direction: str = "OUT"
    book: str = "BUSINESS"


class FormQuickIn(BaseModel):
    loan_id: int
    entry_date: str = ""
    amount: str = Field(max_length=30)
    payment_mode: str = "CASH"
    reference: str = Field("", max_length=200)
    note: str = Field("", max_length=1000)


class FormNoteIn(BaseModel):
    note: str = Field("", max_length=1000)


class FormSimulateIn(BaseModel):
    mode: str = "loan"
    loan_id: int | None = None
    kind: str = "EXTRA_PAYMENT"
    on: str = ""
    amount: str = Field("", max_length=30)
    rate: str = Field("", max_length=20)
    months: str = "1"
    horizon: int = 6
    direction: str = "GIVEN"
    loan_book: str = "BUSINESS"
    start: str = ""
    due_day: str = ""
    maturity: str = ""
    days: int = 60


DOC_KINDS = {"ID_PROOF": "ID proof", "AGREEMENT": "Agreement", "CHEQUE": "Cheque", "PHOTO": "Photo", "OTHER": "Other"}
ENTRY_WORDS = {"PAYMENT": "Payment", "DISBURSE": "Disbursement", "TOPUP": "Top-up", "RATE_CHANGE": "Rate change",
               "PENALTY": "Penalty", "WAIVER": "Waiver", "CHARGE": "Charge", "ADJUSTMENT": "Adjustment"}


def entry_words(e: m.LedgerEntry) -> str:
    return ENTRY_WORDS.get(e.type, e.type.replace("_", " ").capitalize())


def register(app: FastAPI, h: SimpleNamespace) -> None:
    """Add the React app's API. h carries the app's helpers: db, user, owner_only, log, today,
    book_of, parse_date, csrf_token, settings, vault, receipt_of, issuer_for, recent_failures,
    receipt_json, PUBLIC_HEADERS, insecure_secret."""
    db, user, owner_only, log, today = h.db, h.user, h.owner_only, h.log, h.today
    settings, vault, parse_date = h.settings, h.vault, h.parse_date
    BOOKS = h.BOOKS

    def book(request: Request) -> str | None:
        return BOOKS[h.book_of(request)]

    def opt_date(v: str) -> date | None:
        return parse_date(v, None) if v else None

    def money(v: str) -> int:
        return svc.rupees_to_paise(v)

    # ---------------------------------------------------------- session and login
    @app.get("/api/session")
    def api_session(request: Request, s: Session = Depends(db)):
        """Who is logged in, plus the CSRF token every non-GET /api call must send back."""
        u = h.who(request, s)
        device = getattr(request.state, "device_id", None) if u is not None else None
        out = {"csrf": h.csrf_token(request), "user": None, "today": J(today()), "book": h.book_of(request),
               "timezone": settings.timezone, "device_id": device,
               "ended": h.ENDED_MESSAGES.get(request.state.ended) if u is None else None,
               "idle_minutes": settings.app_idle_minutes if device else settings.session_idle_minutes}
        if u is not None:
            overdue = s.scalar(select(func.count()).select_from(m.Loan).where(m.Loan.status == "OVERDUE")) or 0
            out.update(user=u.username, role=u.role, nav_overdue=overdue, insecure_secret=h.insecure_secret,
                       documents_ok=vault.problem is None and u.role == "OWNER", documents_problem=vault.problem,
                       public_url=settings.public_url or None)
        return out

    def check_login(request: Request, payload: LoginIn, s: Session) -> m.AppUser | JSONResponse:
        """Password + authenticator code, with the per-address attempt limit."""
        ip = request.client.host if request.client else "?"
        now = time.time()
        fails = h.recent_failures(app.state.failed_logins, ip, now)
        uname = payload.username.strip()
        if len(fails) >= 5:
            h.log(request, s, "LOGIN_BLOCKED", actor=uname[:100])
            return JSONResponse({"detail": "Too many attempts. Wait 15 minutes."}, status_code=429)
        u = s.scalar(select(m.AppUser).where(m.AppUser.username == uname))
        ok = u is not None and u.disabled_at is None and auth.verify_password(payload.password, u.password_hash)
        if ok and u.totp_secret:
            ok = auth.verify_totp(u.totp_secret, payload.code)
        if not ok:
            fails.append(now)
            h.log(request, s, "LOGIN_FAILED", actor=uname[:100])
            return JSONResponse({"detail": "Wrong username, password or code."}, status_code=401)
        fails.clear()
        return u

    @app.post("/api/device/login", status_code=201)
    def api_device_login(request: Request, payload: DeviceLoginIn, s: Session = Depends(db)):
        """Sign a phone in to the Android app: returns its token once (only a hash is kept)."""
        u = check_login(request, payload, s)
        if isinstance(u, JSONResponse):
            return u
        if settings.single_session:
            svc.end_other_sign_ins(s, u)
        d, token = svc.create_device(s, u, payload.device_name, datetime.now(timezone.utc))
        h.log(request, s, "DEVICE_SIGNED_IN", "app_device", d.id, actor=u.username, name=d.name, role=u.role,
              note="other sign-ins ended" if settings.single_session else None)
        return {"token": token, "device_id": d.id, "user": u.username, "role": u.role}

    @app.post("/api/device/logout")
    def api_device_logout(request: Request, username: str = Depends(user), s: Session = Depends(db)):
        dev = getattr(request.state, "device_id", None)
        if dev is None:
            raise svc.ServiceError("this is not a phone sign-in")
        svc.revoke_device(s, dev, actor=username, now=datetime.now(timezone.utc))
        log(request, s, "DEVICE_SIGNED_OUT", "app_device", dev)
        return {"ok": True}

    @app.get("/api/devices")
    def api_devices(request: Request, username: str = Depends(user), s: Session = Depends(db)):
        """Phones signed in to the app: the owner sees every user's, a viewer only their own."""
        me = s.scalar(select(m.AppUser).where(m.AppUser.username == username))
        rows = svc.list_devices(s, None if request.state.role == "OWNER" else me.id)
        now = datetime.now(timezone.utc)
        idle = timedelta(minutes=settings.app_idle_minutes)
        return [{"id": d.id, "name": d.name, "user": d.user.username, "created_at": J(d.created_at),
                 "last_used_at": J(d.last_used_at), "revoked_at": J(d.revoked_at),
                 "live": svc.resolve_device_state(d, now, idle) == "LIVE", "state": svc.resolve_device_state(d, now, idle),
                 "current": d.id == getattr(request.state, "device_id", None)} for d in rows]

    @app.post("/api/devices/{device_id}/revoke")
    def api_device_revoke(device_id: int, request: Request, username: str = Depends(owner_only),
                          s: Session = Depends(db)):
        d = svc.revoke_device(s, device_id, actor=username, now=datetime.now(timezone.utc))
        log(request, s, "DEVICE_SIGNED_OUT", "app_device", d.id, name=d.name, note=f"user {d.user.username}")
        return {"id": d.id, "message": f"{d.name} is signed out. It must sign in again to open the app."}

    @app.post("/api/login")
    def api_login(request: Request, payload: LoginIn, s: Session = Depends(db)):
        u = check_login(request, payload, s)
        if isinstance(u, JSONResponse):
            return u
        if settings.single_session:
            svc.end_other_sign_ins(s, u)
        request.session.clear()            # new session on login (prevents session fixation)
        request.session["user"] = u.username
        request.session["epoch"] = u.session_epoch
        request.session["start"] = request.session["seen"] = time.time()
        h.log(request, s, "LOGIN_OK", "app_user", u.id, role=u.role,
              note="other sign-ins ended" if settings.single_session else None)
        return {"user": u.username, "role": u.role, "csrf": h.csrf_token(request)}

    @app.post("/api/logout")
    def api_logout(request: Request, s: Session = Depends(db)):
        if request.session.get("user"):
            h.log(request, s, "LOGOUT")
        request.session.clear()
        return {"ok": True, "csrf": h.csrf_token(request)}

    # ---------------------------------------------------------- overview
    @app.get("/api/ui/home")
    def ui_home(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        d = reports.dashboard(s, t, book(request), cash_buffer_paise=settings.cash_buffer_paise,
                              concentration_flag_pct=settings.concentration_flag_pct)
        # a month ago, for the "vs last month" lines on the cards
        prev = reports.dashboard(s, add_months(t, -1), book(request))
        last = s.scalar(select(m.JobRun).order_by(m.JobRun.started_at.desc()).limit(1))
        out = J(d)
        out["trend"] = [{"label": lab, "earned_paise": e, "cost_paise": c} for lab, e, c in d.trend]
        out["concentration"] = [{"name": n, "amount_paise": a, "share_pct": sh, "flagged": f}
                                for n, a, sh, f in d.concentration]
        out["previous"] = {"lent_out_paise": prev.lent_out_paise, "borrowed_paise": prev.borrowed_paise,
                           "own_capital_deployed_paise": prev.own_capital_deployed_paise,
                           "net_interest_month_paise": prev.net_interest_income.get("month", 0),
                           "as_of": J(prev.as_of)}
        out["last_job"] = J(last) if last else None
        return out

    # ---------------------------------------------------------- people
    def party_summary(rows: list[svc.LoanRow]) -> dict:
        lent = sum(r.result.balances.principal_outstanding_paise for r in rows if r.loan.direction == "GIVEN")
        taken = sum(r.result.balances.principal_outstanding_paise for r in rows if r.loan.direction == "TAKEN")
        overdue = sum(d.outstanding_paise for r in rows for d in r.result.dues if d.status == "OVERDUE")
        return {"lent_paise": lent, "taken_paise": taken, "net_paise": lent - taken, "overdue_paise": overdue,
                "open_loans": sum(1 for r in rows if r.status not in CLOSED), "loans": len(rows)}

    @app.get("/api/ui/parties")
    def ui_parties(request: Request, q: str = "", _: str = Depends(user), s: Session = Depends(db)):
        return J(h.reports_parties(s, today(), book(request), q))

    @app.get("/api/ui/parties/{party_id}")
    def ui_party(party_id: int, request: Request, _: str = Depends(user), s: Session = Depends(db)):
        party = svc.get_party(s, party_id)
        rows = svc.portfolio(s, today(), book(request), party_id=party_id)
        owner = request.state.role == "OWNER"
        return {"party": J(party), "loans": [loan_brief(r) for r in rows], "summary": party_summary(rows),
                "documents": [doc_json(x) for x in svc.documents(s, party_id=party_id)] if owner else None}

    @app.post("/api/parties/{party_id}")
    def api_party_update(party_id: int, request: Request, payload: PartyUpdateIn, _: str = Depends(user),
                         s: Session = Depends(db)):
        p = svc.update_party(s, party_id, **payload.model_dump())
        log(request, s, "PARTY_UPDATED", "party", party_id, name=payload.name)
        return {"id": party_id, "name": getattr(p, "name", payload.name), "message": "Saved."}

    # ---------------------------------------------------------- loans
    @app.get("/api/ui/loans")
    def ui_loans(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        """All loans of the book; the screen filters by direction / status / text itself."""
        return [loan_brief(r) for r in svc.portfolio(s, today(), book(request))]

    @app.get("/api/ui/parties-min")
    def ui_parties_min(_: str = Depends(user), s: Session = Depends(db)):
        return [{"id": p.id, "name": p.name, "phone": p.phone} for p in svc.list_parties(s)]

    @app.post("/api/ui/loans", status_code=201)
    def ui_loan_create(request: Request, p: FormLoanIn, _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        loan = svc.create_loan(
            s, party_id=p.party_id, direction=p.direction, book=p.book, principal_paise=money(p.principal),
            start_date=parse_date(p.start_date, t), rate=svc.parse_rate(p.rate), rate_unit=p.rate_unit,
            interest_cycle=p.interest_cycle, due_day=int(p.due_day) if p.due_day.strip().isdigit() else None,
            grace_days=p.grace_days, maturity_date=opt_date(p.maturity_date), purpose=p.purpose,
            collateral=p.collateral, payment_mode=p.payment_mode, as_of=t)
        log(request, s, "LOAN_CREATED", "loan", loan.id, direction=loan.direction, book=loan.book,
            principal_paise=loan.principal_paise, rate=loan.rate)
        return {"id": loan.id, "message": "Loan created."}

    @app.get("/api/ui/loans/{loan_id}")
    def ui_loan(loan_id: int, request: Request, _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        loan = svc.get_loan(s, loan_id)
        res = svc.compute_loan(loan, t)
        row = svc.LoanRow(loan, res)
        reversed_ids = {e.reverses_id for e in loan.entries if e.reverses_id}
        allocs: dict[int, list] = defaultdict(list)
        for a in res.allocations:
            allocs[a.entry_id].append({"bucket": a.bucket, "amount_paise": a.amount_paise,
                                       "due_period_start": J(a.due_period_start)})
        rcpts = {r.ledger_entry_id: r for r in svc.receipts(s, loan_id=loan_id)}
        entries = []
        for e in sorted(loan.entries, key=lambda x: (x.entry_date, x.id), reverse=True):
            rc = rcpts.get(e.id)
            entries.append(J(e) | {"words": entry_words(e), "reversed": e.id in reversed_ids,
                                   "allocations": allocs.get(e.id, []),
                                   "receipt": {"id": rc.id, "number": rc.number,
                                               "cancelled": rc.cancelled_at is not None} if rc else None})
        links = [l for l in svc.funding_links(s) if loan_id in (l.taken_loan_id, l.given_loan_id)]
        others = {l2.id: l2 for l2 in s.scalars(select(m.Loan).where(
            m.Loan.id.in_([l.taken_loan_id for l in links] + [l.given_loan_id for l in links])))}
        link_rows = [{"id": l.id, "amount_paise": l.amount_paise, "link_date": J(l.link_date),
                      "other_loan_id": (l.given_loan_id if l.taken_loan_id == loan_id else l.taken_loan_id),
                      "other_party": others[(l.given_loan_id if l.taken_loan_id == loan_id
                                             else l.taken_loan_id)].party.name} for l in links]
        contacts, promises, wa = [], [], None
        if loan.direction == "GIVEN":
            from loan_tracker.collections import reminder_text, wa_link
            acts, proms = svc.loan_collections(s, loan_id)
            contacts = [J(a) for a in acts]
            states = [(p, svc.promise_state(p, loan, t)) for p in proms]
            promises = [J(p) | {"state": J(st)} for p, st in states]
            late = [d for d in res.dues if d.status == "OVERDUE"]
            pend_p, pending = next(((p, st) for p, st in sorted(states, key=lambda x: x[0].promised_date)
                                    if st.status == "PENDING"), (None, None))
            wa = wa_link(loan.party.phone, reminder_text(
                loan.party.name, loan.id,
                overdue_paise=sum(d.outstanding_paise for d in late),
                overdue_since=late[0].due.due_date if late else None,
                next_paise=res.projected_due.interest_paise if res.projected_due else 0,
                next_date=res.projected_due.due_date if res.projected_due else None,
                promise_paise=pending.short_paise if pending else 0,
                promise_date=pend_p.promised_date if pend_p else None))
        owner = request.state.role == "OWNER"
        live = row.status
        dues = [{"period_start": J(d.due.period_start), "period_end": J(d.due.period_end),
                 "due_date": J(d.due.due_date), "interest_paise": d.due.interest_paise, "paid_paise": d.paid_paise,
                 "waived_paise": d.waived_paise, "penalty_paise": d.penalty_paise, "status": d.status,
                 "outstanding_paise": d.outstanding_paise, "overdue_days": d.overdue_days}
                for d in sorted(res.dues, key=lambda x: x.due.due_date, reverse=True)]
        return {
            "loan": J(loan) | {"party_phone": loan.party.phone}, "brief": loan_brief(row), "status": live,
            "balances": J(res.balances), "projected_due": J(res.projected_due), "dues": dues, "entries": entries,
            "links": link_rows, "contacts": contacts, "promises": promises, "whatsapp": wa,
            "chasing": loan.direction == "GIVEN" and live not in ("CLOSED", "SETTLED"),
            "can_close": loan.direction == "GIVEN" and live in ("CLOSED", "SETTLED"),
            "missing_receipts": len(svc.payments_without_receipt(s, loan_id)) if loan.direction == "GIVEN" else 0,
            "shares": [J(x) | {"live": x.revoked_at is None and x.expires_at > datetime.now(timezone.utc)}
                       for x in svc.share_links(s, loan_id)] if loan.direction == "GIVEN" else [],
            "documents": [doc_json(x) for x in svc.documents(s, loan_id=loan_id)] if owner else None,
        }

    def entry_message(e: m.LedgerEntry, res) -> str:
        if e.type == "PAYMENT":
            split = ", ".join(f"{a.bucket.lower()} {fmt.inr(a.amount_paise)}"
                              for a in res.allocations if a.entry_id == e.id)
            return f"Payment of {fmt.inr(e.amount_paise)} recorded: {split or 'held as credit'}."
        msg = entry_words(e)
        msg += f" of {fmt.inr(e.amount_paise)}" if e.amount_paise else ""
        msg += f" to {e.new_rate.normalize():f}% a month" if e.type == "RATE_CHANGE" and e.new_rate is not None else ""
        return msg + f" on {e.entry_date:%d %b %Y} recorded."

    @app.post("/api/ui/loans/{loan_id}/entries", status_code=201)
    def ui_entry(loan_id: int, request: Request, p: FormEntryIn, _: str = Depends(user),
                 s: Session = Depends(db)):
        t = today()
        e, res = svc.post_entry(s, loan_id, type=p.type, entry_date=parse_date(p.entry_date, t), as_of=t,
                                amount_paise=money(p.amount) if p.amount.strip() else None,
                                new_rate=p.new_rate or None, payment_mode=p.payment_mode,
                                reference=p.reference, note=p.note)
        log(request, s, "ENTRY_POSTED", "ledger_entry", e.id, loan_id=loan_id, type=e.type,
            amount_paise=e.amount_paise, entry_date=e.entry_date)
        msg = entry_message(e, res)
        rc = h.receipt_of(s, e.id)
        if rc:
            msg += f" Receipt {rc.number} issued."
        return {"id": e.id, "message": msg, "receipt_id": rc.id if rc else None}

    @app.post("/api/ui/entries/{entry_id}/reverse")
    def ui_reverse(entry_id: int, request: Request, p: FormNoteIn, _: str = Depends(user),
                   s: Session = Depends(db)):
        t = today()
        original = s.get(m.LedgerEntry, entry_id)
        if original is None:
            raise svc.ServiceError(f"entry {entry_id} not found")
        svc.reverse_entry(s, entry_id, entry_date=t, as_of=t, note=p.note)
        log(request, s, "ENTRY_REVERSED", "ledger_entry", entry_id, loan_id=original.loan_id, type=original.type,
            amount_paise=original.amount_paise, note=p.note or None)
        what = entry_words(original) + (f" of {fmt.inr(original.amount_paise)}" if original.amount_paise else "")
        return {"loan_id": original.loan_id,
                "message": f"{what} on {original.entry_date:%d %b %Y} reversed (entry #{entry_id})."}

    @app.post("/api/loans/{loan_id}/status")
    def api_loan_status(loan_id: int, request: Request, p: StatusIn, _: str = Depends(user),
                        s: Session = Depends(db)):
        before = svc.get_loan(s, loan_id).status
        svc.set_owner_status(s, loan_id, p.status or None, today())
        log(request, s, "STATUS_CHANGED", "loan", loan_id, before=before, after=p.status or "ACTIVE")
        return {"id": loan_id, "message": "Status updated."}

    @app.post("/api/ui/loans/{loan_id}/contacts", status_code=201)
    def ui_contact(loan_id: int, request: Request, p: FormContactIn, username: str = Depends(user),
                   s: Session = Depends(db)):
        t = today()
        act, promise = svc.log_contact(
            s, loan_id, actor=username, as_of=t, contact_date=parse_date(p.contact_date, t), kind=p.kind,
            outcome=p.outcome, note=p.note, next_follow_up=opt_date(p.next_follow_up),
            promise_amount_paise=money(p.promise_amount) if p.promise_amount.strip() else None,
            promise_date=opt_date(p.promise_date))
        log(request, s, "CONTACT_LOGGED", "collection_activity", act.id, loan_id=loan_id, type=p.kind,
            outcome=p.outcome, note=act.note)
        if promise:
            log(request, s, "PROMISE_MADE", "payment_promise", promise.id, loan_id=loan_id,
                amount_paise=promise.amount_paise, promised_date=promise.promised_date)
        msg = "Contact saved." + (f" Promise of {fmt.inr(promise.amount_paise)} by {fmt.dmy(promise.promised_date)} "
                                  "is tracked against real payments." if promise else "")
        return {"id": act.id, "message": msg}

    @app.post("/api/ui/promises/{promise_id}/cancel")
    def ui_promise_cancel(promise_id: int, request: Request, p: FormNoteIn, _: str = Depends(user),
                          s: Session = Depends(db)):
        pr = svc.cancel_promise(s, promise_id, p.note)
        log(request, s, "PROMISE_CANCELLED", "payment_promise", pr.id, loan_id=pr.loan_id,
            amount_paise=pr.amount_paise, note=pr.cancel_note)
        return {"id": pr.id, "message": "Promise cancelled."}

    # ---------------------------------------------------------- share links
    @app.post("/api/loans/{loan_id}/share", status_code=201)
    def api_share(loan_id: int, request: Request, p: ShareIn, username: str = Depends(user),
                  s: Session = Depends(db)):
        link, token = svc.create_share_link(s, loan_id, days=p.days, actor=username, now=datetime.now(timezone.utc))
        log(request, s, "SHARE_CREATED", "share_link", link.id, loan_id=loan_id, note=f"{p.days} days")
        base = settings.public_url or str(request.base_url).rstrip("/")
        url = f"{base}/s/{token}"
        loan = svc.get_loan(s, loan_id)
        from loan_tracker.collections import wa_link
        wa = wa_link(loan.party.phone, f"Hello {loan.party.name.split()[0]}, here is your loan statement "
                                       f"(LN-{loan.id}): {url} . The link is private and works for {p.days} days.")
        return {"id": link.id, "url": url, "days": p.days, "whatsapp": wa,
                "message": "Link created. Copy it now: it is shown only once."}

    @app.post("/api/share/{link_id}/revoke")
    def api_share_revoke(link_id: int, request: Request, username: str = Depends(user), s: Session = Depends(db)):
        link = svc.revoke_share_link(s, link_id, actor=username, now=datetime.now(timezone.utc))
        log(request, s, "SHARE_REVOKED", "share_link", link.id, loan_id=link.loan_id)
        return {"id": link.id, "message": "Link switched off. It no longer opens."}

    # ---------------------------------------------------------- collections, payments
    @app.get("/api/ui/collections")
    def ui_collections(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        return J(reports.collections_overview(s, today(), book(request)))

    @app.get("/api/ui/payments")
    def ui_payments(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        """Every payment in (from borrowers) and out (to lenders), newest first."""
        b = book(request)
        q = (select(m.LedgerEntry, m.Loan).join(m.Loan, m.Loan.id == m.LedgerEntry.loan_id)
             .where(m.LedgerEntry.type == "PAYMENT").order_by(m.LedgerEntry.entry_date.desc(), m.LedgerEntry.id.desc())
             .limit(2000))
        if b:
            q = q.where(m.Loan.book == b)
        pairs = s.execute(q).all()
        reversed_ids = set(s.scalars(select(m.LedgerEntry.reverses_id).where(m.LedgerEntry.reverses_id.is_not(None))))
        ids = [e.id for e, _ in pairs]
        rc = {r.ledger_entry_id: r for r in s.scalars(select(m.Receipt).where(m.Receipt.ledger_entry_id.in_(ids)))} if ids else {}
        alloc: dict[int, dict] = defaultdict(lambda: {"PENALTY": 0, "INTEREST": 0, "PRINCIPAL": 0, "CREDIT": 0})
        for a in s.scalars(select(m.Allocation).where(m.Allocation.ledger_entry_id.in_(ids))) if ids else []:
            alloc[a.ledger_entry_id][a.bucket] = alloc[a.ledger_entry_id].get(a.bucket, 0) + a.amount_paise
        return [{"id": e.id, "loan_id": l.id, "party_id": l.party_id, "party_name": l.party.name,
                 "direction": "IN" if l.direction == "GIVEN" else "OUT", "book": l.book,
                 "entry_date": J(e.entry_date), "amount_paise": e.amount_paise, "payment_mode": e.payment_mode,
                 "reference": e.reference, "note": e.note, "reversed": e.id in reversed_ids,
                 "split": alloc.get(e.id) or None,
                 "receipt": {"id": rc[e.id].id, "number": rc[e.id].number} if e.id in rc else None}
                for e, l in pairs]

    @app.post("/api/ui/quick", status_code=201)
    def ui_quick(request: Request, p: FormQuickIn, _: str = Depends(user), s: Session = Depends(db)):
        """Record payment from anywhere (the top-bar button)."""
        t = today()
        loan_id = p.loan_id
        e, res = svc.post_entry(s, loan_id, type="PAYMENT", entry_date=parse_date(p.entry_date, t), as_of=t,
                                amount_paise=money(p.amount), payment_mode=p.payment_mode, reference=p.reference,
                                note=p.note)
        log(request, s, "ENTRY_POSTED", "ledger_entry", e.id, loan_id=loan_id, type=e.type,
            amount_paise=e.amount_paise, entry_date=e.entry_date, via="quick")
        rc = h.receipt_of(s, e.id)
        return {"id": e.id, "loan_id": loan_id, "receipt_id": rc.id if rc else None,
                "message": entry_message(e, res).replace("recorded:", f"recorded on LN-{loan_id}:")
                + (f" Receipt {rc.number} issued." if rc else "")}

    # ---------------------------------------------------------- search (Ctrl K)
    @app.get("/api/search")
    def api_search(request: Request, q: str = "", _: str = Depends(user), s: Session = Depends(db)):
        needle = q.strip()
        if len(needle) < 2:
            return {"people": [], "loans": [], "receipts": []}
        like = f"%{needle}%"
        people = s.scalars(select(m.Party).where(or_(m.Party.name.ilike(like), m.Party.phone.ilike(like)))
                           .order_by(m.Party.name).limit(8)).all()
        up = needle.upper().removeprefix("LN-").removeprefix("LN")
        loan_q = select(m.Loan).join(m.Party).where(m.Party.name.ilike(like))
        if up.strip().isdigit():
            loan_q = select(m.Loan).join(m.Party).where(or_(m.Loan.id == int(up.strip()), m.Party.name.ilike(like)))
        loans = s.scalars(loan_q.order_by(m.Loan.id.desc()).limit(8)).all()
        rc = s.scalars(select(m.Receipt).where(m.Receipt.number.ilike(like)).order_by(m.Receipt.id.desc()).limit(8)).all()
        return {"people": [{"id": p.id, "name": p.name, "phone": p.phone} for p in people],
                "loans": [{"id": l.id, "party_name": l.party.name, "direction": l.direction, "book": l.book,
                           "principal_paise": l.principal_paise, "status": l.status} for l in loans],
                "receipts": [{"id": r.id, "number": r.number, "party_name": r.loan.party.name,
                              "amount_paise": r.amount_paise, "payment_date": J(r.payment_date)} for r in rc]}

    # ---------------------------------------------------------- funding
    @app.get("/api/ui/funding")
    def ui_funding(_: str = Depends(user), s: Session = Depends(db)):
        from loan_tracker import engine as eng
        rows = svc.portfolio(s, today())
        by_id = {r.loan.id: r for r in rows}
        links = svc.funding_links(s)
        terms = {r.loan.id: svc.to_terms(r.loan) for r in rows}
        rep = eng.spread([eng.FundingLink(l.taken_loan_id, l.given_loan_id, l.amount_paise, l.link_date)
                          for l in links], terms,
                         outstanding={r.loan.id: r.result.balances.principal_outstanding_paise for r in rows})
        used_taken: dict[int, int] = defaultdict(int)
        used_given: dict[int, int] = defaultdict(int)
        for l in links:
            used_taken[l.taken_loan_id] += l.amount_paise
            used_given[l.given_loan_id] += l.amount_paise
        return {
            "links": [J(l) | {"spread": J(sp), "taken": loan_brief(by_id[l.taken_loan_id]),
                              "given": loan_brief(by_id[l.given_loan_id])} for l, sp in zip(links, rep.links)],
            "idle_cost_paise_per_cycle": rep.idle_cost_paise_per_cycle,
            "taken": [loan_brief(r) | {"unlinked_paise": r.loan.principal_paise - used_taken[r.loan.id]}
                      for r in rows if r.loan.direction == "TAKEN"],
            "given": [loan_brief(r) | {"unfunded_paise": r.loan.principal_paise - used_given[r.loan.id]}
                      for r in rows if r.loan.direction == "GIVEN"],
        }

    @app.post("/api/ui/funding", status_code=201)
    def ui_funding_create(request: Request, p: FormFundingIn, _: str = Depends(user), s: Session = Depends(db)):
        link = svc.link_funding(s, taken_loan_id=p.taken_loan_id, given_loan_id=p.given_loan_id,
                                amount_paise=money(p.amount), link_date=parse_date(p.link_date, today()),
                                confirm_cross_book=p.confirm_cross_book)
        log(request, s, "FUNDING_LINKED", "funding_link", link.id, taken_loan_id=p.taken_loan_id,
            given_loan_id=p.given_loan_id, amount_paise=link.amount_paise)
        return {"id": link.id, "message": "Funding link saved."}

    # ---------------------------------------------------------- activity
    @app.get("/api/ui/activity")
    def ui_activity(loan_id: int | None = None, _: str = Depends(user), s: Session = Depends(db)):
        def label(a: str) -> tuple[str, str]:
            return fmt.ACTION_LABELS.get(a, (a.replace("_", " ").capitalize(), "gray"))
        return [J(r) | {"label": label(r.action)[0], "tone": label(r.action)[1]}
                for r in svc.audit_log(s, limit=300, loan_id=loan_id)]

    # ---------------------------------------------------------- calendar and cash
    def forecast(f) -> dict:
        def one(x):
            return {"short_date": J(x.short_date), "short_paise": x.short_paise, "low_paise": x.low_paise,
                    "low_date": J(getattr(x, "low_date", None)),
                    "days": [{"day": J(d.day), "running_paise": d.running_paise} for d in x.days]}
        return {"as_of": J(f.as_of), "days": f.days, "opening_paise": f.opening_paise,
                "buffer_paise": f.buffer_paise, "accounts": [{"account": J(a), "balance": J(b)} for a, b in f.accounts],
                "calendar": J(f.calendar), "expected": one(f.expected), "cautious": one(f.cautious)}

    @app.get("/api/ui/calendar")
    def ui_calendar(request: Request, days: int = 30, _: str = Depends(user), s: Session = Depends(db)):
        days = days if days in (30, 60, 90) else 30
        t = today()
        f = reports.cash_forecast(s, t, days, book(request), settings.cash_buffer_paise)
        cal = f.calendar
        tin = sum(i.amount_paise for d in cal for i in d.money_in)
        tout = sum(i.amount_paise for d in cal for i in d.money_out)
        return {"days": days, "today": J(t), "calendar": J(cal),
                "opening_paise": f.opening_paise,
                "running": [{"day": J(d.day), "running_paise": d.running_paise} for d in f.expected.days],
                "totals": {"in_paise": tin, "out_paise": tout, "net_paise": tin - tout,
                           "low_paise": f.expected.low_paise, "low_day": J(f.expected.short_date)}}

    @app.get("/api/ui/cash")
    def ui_cash(request: Request, days: int = 30, _: str = Depends(user), s: Session = Depends(db)):
        days = days if days in (30, 60, 90) else 30
        t = today()
        b = book(request)
        f = reports.cash_forecast(s, t, days, b, settings.cash_buffer_paise)
        closed = [a for a, _ in svc.latest_balances(s, b, include_inactive=True) if not a.is_active]
        return {"forecast": forecast(f), "warnings": reports.cash_warnings(f),
                "planned": [J(p) for p in svc.planned_items(s, t, t + timedelta(days=365), b)],
                "closed_accounts": [J(a) for a in closed], "today": J(t)}

    @app.post("/api/ui/cash/accounts", status_code=201)
    def ui_cash_account(request: Request, p: FormCashAccountIn, _: str = Depends(user), s: Session = Depends(db)):
        acc = svc.create_cash_account(s, name=p.name, kind=p.kind, book=p.book,
                                      balance_paise=money(p.balance or "0"), as_of=today())
        log(request, s, "CASH_ACCOUNT_ADDED", "cash_account", acc.id, name=acc.name, book=acc.book,
            amount_paise=money(p.balance or "0"))
        return {"id": acc.id, "message": f"Account {acc.name} added."}

    @app.post("/api/ui/cash/accounts/{account_id}/balance", status_code=201)
    def ui_cash_balance(account_id: int, request: Request, p: FormBalanceIn, _: str = Depends(user),
                        s: Session = Depends(db)):
        t = today()
        b = svc.set_cash_balance(s, account_id, balance_paise=money(p.balance), on=parse_date(p.on, t), as_of=t,
                                 note=p.note)
        log(request, s, "CASH_BALANCE_SET", "cash_account", account_id, amount_paise=b.balance_paise,
            on=b.as_of, note=b.note)
        return {"id": b.id, "message": f"Balance {fmt.inr(b.balance_paise)} saved for {fmt.dmy(b.as_of)}."}

    @app.post("/api/cash/accounts/{account_id}/active")
    def api_cash_active(account_id: int, request: Request, p: ActiveIn, _: str = Depends(user),
                        s: Session = Depends(db)):
        acc = svc.set_cash_account_active(s, account_id, p.active)
        log(request, s, "CASH_ACCOUNT_REOPENED" if acc.is_active else "CASH_ACCOUNT_CLOSED", "cash_account",
            acc.id, name=acc.name)
        return {"id": acc.id, "message": f"{acc.name} {'reopened' if acc.is_active else 'closed'}."}

    @app.post("/api/ui/cash/planned", status_code=201)
    def ui_planned(request: Request, p: FormPlannedIn, _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        it = svc.add_planned_item(s, book=p.book, item_date=parse_date(p.item_date, t), direction=p.direction,
                                  amount_paise=money(p.amount), description=p.description, as_of=t)
        log(request, s, "PLAN_ADDED", "planned_cash_item", it.id, note=it.description,
            amount_paise=it.amount_paise, on=it.item_date, type=it.direction)
        return {"id": it.id, "message": "Planned item added to the forecast."}

    # ---------------------------------------------------------- what-if
    @app.get("/api/ui/simulate")
    def ui_simulate_loans(_: str = Depends(user), s: Session = Depends(db)):
        from loan_tracker.simulator import KINDS
        return {"loans": [loan_brief(r) for r in svc.portfolio(s, today()) if r.status not in CLOSED],
                "kinds": list(KINDS), "today": J(today())}

    @app.post("/api/ui/simulate")
    def ui_simulate(p: FormSimulateIn, _: str = Depends(user), s: Session = Depends(db)):
        """Nothing is saved: a projection only."""
        from loan_tracker.simulator import KINDS, Scenario
        t = today()
        if p.mode == "new":
            start = parse_date(p.start, t)
            sim = reports.simulate_new_loan(
                s, t, p.days if p.days in (30, 60, 90, 180) else 60,
                direction=p.direction if p.direction in ("GIVEN", "TAKEN") else "GIVEN",
                book=p.loan_book if p.loan_book in ("BUSINESS", "PERSONAL") else "BUSINESS",
                principal_paise=money(p.amount or "0"), rate=svc.parse_rate(p.rate or "0"), start_date=start,
                due_day=int(p.due_day) if p.due_day.isdigit() else start.day,
                maturity_date=opt_date(p.maturity), buffer_paise=settings.cash_buffer_paise)
            return {"mode": "new", "result": J(sim)}
        if p.loan_id is None:
            raise svc.ServiceError("choose a loan")
        sc = Scenario(p.kind if p.kind in KINDS else "EXTRA_PAYMENT", parse_date(p.on, t),
                      amount_paise=money(p.amount) if p.amount.strip() else 0,
                      new_rate=svc.parse_rate(p.rate) if p.rate.strip() else None,
                      months=int(p.months) if p.months.isdigit() else 0)
        horizon = p.horizon if p.horizon in (3, 6, 12, 24) else 6
        sim = reports.simulate_loan(s, p.loan_id, t, add_months(t, horizon), sc)
        return {"mode": "loan", "result": J(sim)}

    # ---------------------------------------------------------- documents
    def doc_json(d: m.Document) -> dict:
        return {"id": d.id, "party_id": d.party_id, "party_name": d.party.name if d.party else None,
                "loan_id": d.loan_id, "kind": d.kind, "kind_label": DOC_KINDS.get(d.kind, d.kind),
                "title": d.title, "file_name": d.file_name, "content_type": d.content_type,
                "size_bytes": d.size_bytes, "uploaded_by": d.uploaded_by, "created_at": J(d.created_at)}

    @app.get("/api/ui/documents")
    def ui_documents(_: str = Depends(owner_only), s: Session = Depends(db)):
        return {"documents": [doc_json(d) for d in svc.documents(s)], "problem": vault.problem,
                "kinds": DOC_KINDS,
                "people": [{"id": p.id, "name": p.name} for p in svc.list_parties(s)],
                "loans": [{"id": r.loan.id, "party_id": r.loan.party_id, "direction": r.loan.direction,
                           "principal_paise": r.loan.principal_paise} for r in svc.portfolio(s, today())]}

    @app.post("/api/documents", status_code=201)
    async def api_document_upload(request: Request, file: UploadFile = File(...), kind: str = Form("OTHER"),
                                  title: str = Form(""), party_id: str = Form(""), loan_id: str = Form(""),
                                  username: str = Depends(owner_only), s: Session = Depends(db)):
        from loan_tracker.docvault import MAX_DOC_BYTES
        data = await file.read(MAX_DOC_BYTES + 1)
        d = svc.store_document(s, vault, data=data, file_name=file.filename or "", kind=kind, title=title,
                               actor=username, party_id=int(party_id) if party_id.isdigit() else None,
                               loan_id=int(loan_id) if loan_id.isdigit() else None)
        log(request, s, "DOC_UPLOADED", "document", d.id, name=d.title, type=d.kind, loan_id=d.loan_id,
            note=f"{d.size_bytes // 1024 or 1} KB")
        return doc_json(d) | {"message": f"“{d.title}” stored, encrypted."}

    @app.post("/api/documents/{doc_id}/delete")
    def api_document_delete(doc_id: int, request: Request, username: str = Depends(owner_only),
                            s: Session = Depends(db)):
        d = svc.delete_document(s, vault, doc_id, username)
        log(request, s, "DOC_DELETED", "document", d.id, name=d.title, loan_id=d.loan_id)
        return {"id": d.id, "message": f"“{d.title}” deleted; the file is erased."}

    # ---------------------------------------------------------- bank import
    @app.get("/api/ui/bank")
    def ui_bank(_: str = Depends(user), s: Session = Depends(db)):
        accounts = [a for a, _ in svc.latest_balances(s) if a.kind in ("BANK", "WALLET")]
        imports = svc.bank_imports(s)
        return {"accounts": [J(a) for a in accounts],
                "imports": [J(i) | {"account": i.account.name,
                                    "open": sum(1 for t in svc.bank_txns(s, i.id) if t.status == "NEW")}
                            for i in imports]}

    @app.post("/api/bank/upload", status_code=201)
    async def api_bank_upload(request: Request, account_id: int = Form(...), file: UploadFile = File(...),
                              update_balance: str = Form(""), username: str = Depends(user),
                              s: Session = Depends(db)):
        from loan_tracker.bankimport import MAX_BYTES
        data = await file.read(MAX_BYTES + 1)
        imp, bal = svc.import_statement(s, account_id, data, file_name=file.filename or "statement.csv",
                                        actor=username, as_of=today(), update_balance=update_balance == "1")
        log(request, s, "BANK_IMPORTED", "bank_import", imp.id, name=imp.file_name, count=imp.new_count,
            note=f"{imp.row_count} lines, {imp.row_count - imp.new_count} already imported")
        msg = f"{imp.new_count} new transaction(s) imported"
        if imp.row_count != imp.new_count:
            msg += f"; {imp.row_count - imp.new_count} already imported earlier were skipped"
        if bal:
            msg += f". Balance set to {fmt.inr(bal.balance_paise)} as on {fmt.dmy(bal.as_of)}"
        elif update_balance == "1":
            newest = next((b for a, b in svc.latest_balances(s, include_inactive=True)
                           if a.id == account_id and b is not None), None)
            if newest is not None and imp.period_end and newest.as_of > imp.period_end:
                msg += f". Balance not changed: you have a newer one ({fmt.dmy(newest.as_of)})"
        return {"id": imp.id, "message": msg + "."}

    @app.get("/api/ui/bank/imports/{import_id}")
    def ui_bank_import(import_id: int, show: str = "open", _: str = Depends(user), s: Session = Depends(db)):
        imp = s.get(m.BankImport, import_id)
        if imp is None:
            raise svc.ServiceError(f"import {import_id} not found")
        t = today()
        txns = svc.bank_txns(s, import_id)
        counts = {k: sum(1 for x in txns if x.status == k) for k in ("NEW", "LINKED", "RECORDED", "IGNORED")}
        shown = [x for x in txns if x.status == "NEW"] if show == "open" else txns
        sugg = svc.txn_suggestions(s, t, shown)
        exact = sum(1 for v in sugg.values() if sum(1 for x in v if x.kind == "EXISTING" and x.strong) == 1)
        loans = [r for r in svc.portfolio(s, t) if r.status not in CLOSED]
        names = {r.loan.id: r.loan.party.name for r in svc.portfolio(s, t)}
        ids = [x.ledger_entry_id for x in shown if x.ledger_entry_id]
        entry_loans = dict(s.execute(select(m.LedgerEntry.id, m.LedgerEntry.loan_id)
                                     .where(m.LedgerEntry.id.in_(ids))).all()) if ids else {}
        return {"import": J(imp) | {"account": imp.account.name}, "counts": counts, "show": show, "exact": exact,
                "txns": [J(x) | {"suggestions": [J(g) | {"party_name": names.get(g.loan_id)} for g in sugg.get(x.id, [])],
                                 "entry_loan_id": entry_loans.get(x.ledger_entry_id)} for x in shown],
                "loans": [{"id": r.loan.id, "party_name": r.loan.party.name, "direction": r.loan.direction,
                           "book": r.loan.book} for r in loans]}

    @app.post("/api/bank/imports/{import_id}/link-exact")
    def api_bank_link_exact(import_id: int, request: Request, _: str = Depends(user), s: Session = Depends(db)):
        done = svc.link_exact_matches(s, import_id, today())
        for t in done:
            log(request, s, "BANK_LINKED", "bank_txn", t.id, entry=t.ledger_entry_id, amount_paise=abs(t.amount_paise))
        return {"linked": len(done), "message": f"{len(done)} bank line(s) linked to entries already in your ledger."}

    # ---------------------------------------------------------- reports, statement, closure
    @app.get("/api/ui/reports")
    def ui_reports(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        return {"fy": J(reports.fy_interest_report(s, t)), "overdue": J(reports.overdue_report(s, t, book(request))),
                "cross_book": J(reports.cross_book_report(s, t))}

    @app.get("/api/ui/statement/{loan_id}")
    def ui_statement(loan_id: int, request: Request, _: str = Depends(user), s: Session = Depends(db)):
        return ui_loan(loan_id, request, _, s) | {"issuer": h.issuer_for(svc.get_loan(s, loan_id).book)}

    @app.get("/api/ui/closure/{loan_id}")
    def ui_closure(loan_id: int, _: str = Depends(user), s: Session = Depends(db)):
        c = reports.closure_summary(s, loan_id, today())
        return J(c) | {"issuer": h.issuer_for(c.loan.book), "party": J(c.loan.party)}

    # ---------------------------------------------------------- receipts
    @app.get("/api/ui/receipts")
    def ui_receipts(q: str = "", _: str = Depends(user), s: Session = Depends(db)):
        return {"receipts": [h.receipt_json(r) for r in svc.receipts(s, q=q)],
                "missing": len(svc.payments_without_receipt(s))}

    @app.post("/api/receipts/backfill")
    def api_backfill(request: Request, p: BackfillIn, _: str = Depends(user), s: Session = Depends(db)):
        made = svc.issue_missing_receipts(s, today(), p.loan_id)
        if made:
            log(request, s, "RECEIPTS_ISSUED", "receipt", made[0].id, count=len(made),
                first=made[0].number, last=made[-1].number)
        return {"issued": len(made), "message": f"{len(made)} receipt(s) issued for older payments."
                if made else "Every payment already has a receipt."}

    @app.get("/api/ui/receipts/{receipt_id}")
    def ui_receipt(receipt_id: int, _: str = Depends(user), s: Session = Depends(db)):
        from loan_tracker.collections import wa_link
        from loan_tracker.receipts import amount_in_words
        r = svc.get_receipt(s, receipt_id)
        text = (f"Receipt {r.number}: received {fmt.inr(r.amount_paise)} on {fmt.dmy(r.payment_date)} "
                f"for loan LN-{r.loan_id}"
                + (f" (interest {fmt.inr(r.interest_paise)}" + (f", principal {fmt.inr(r.principal_paise)}"
                                                               if r.principal_paise else "") + ")"
                   if r.interest_paise or r.principal_paise else "")
                + f". Principal outstanding {fmt.inr(r.principal_left_paise)}. Thank you.")
        return h.receipt_json(r) | {"amount_in_words": amount_in_words(r.amount_paise), "issuer": h.issuer_for(r.loan.book),
                                    "party_phone": r.loan.party.phone, "loan_book": r.loan.book,
                                    "whatsapp": None if r.cancelled_at else wa_link(r.loan.party.phone, text)}

    # ---------------------------------------------------------- public borrower pages (no session)
    def public(body: dict | None, status: int = 200, message: str | None = None) -> JSONResponse:
        return JSONResponse(body if body is not None else {"detail": message}, status_code=status,
                            headers=h.PUBLIC_HEADERS)

    def open_link(request: Request, token: str, s: Session):
        ip = request.client.host if request.client else "?"
        now_ts = time.time()
        fails = h.recent_failures(app.state.failed_shares, ip, now_ts)
        if len(fails) >= 30:
            return None, public(None, 429, "Too many attempts. Please try again later.")
        try:
            return svc.resolve_share_link(s, token, datetime.now(timezone.utc)), None
        except svc.LinkUnavailable as exc:
            fails.append(now_ts)
            return None, public(None, 410 if "not valid" not in str(exc) else 404, str(exc))

    @app.get("/api/public/s/{token}")
    def public_statement(token: str, request: Request, s: Session = Depends(db)):
        link, refusal = open_link(request, token, s)
        if refusal:
            return refusal
        svc.record_share_view(s, link, datetime.now(timezone.utc))
        log(request, s, "SHARE_VIEWED", "share_link", link.id, actor="borrower", loan_id=link.loan_id)
        st = svc.borrower_statement(s, link.loan_id, today())
        return public({"statement": J(st), "expires_at": J(link.expires_at), "today": J(today()),
                       "issuer": h.issuer_for(s.get(m.Loan, link.loan_id).book)})

    @app.get("/api/public/s/{token}/receipts/{receipt_id}")
    def public_receipt(token: str, receipt_id: int, request: Request, s: Session = Depends(db)):
        from loan_tracker.receipts import amount_in_words
        link, refusal = open_link(request, token, s)
        if refusal:
            return refusal
        r = s.get(m.Receipt, receipt_id)
        if r is None or r.loan_id != link.loan_id:            # only receipts of this link's loan
            return public(None, 404, "This receipt is not available.")
        log(request, s, "SHARE_VIEWED", "share_link", link.id, actor="borrower", loan_id=link.loan_id, note=r.number)
        body = h.receipt_json(r) | {"amount_in_words": amount_in_words(r.amount_paise),
                                    "issuer": h.issuer_for(r.loan.book), "expires_at": J(link.expires_at)}
        return public(body)
