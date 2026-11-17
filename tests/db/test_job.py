"""Daily job: digest content, never-muted payables, idempotent re-runs, failures recorded."""
from dataclasses import replace
from datetime import date as D
from decimal import Decimal

from sqlalchemy import func, select

from loan_tracker import services as svc
from loan_tracker.config import Settings
from loan_tracker.db import models as m
from loan_tracker.jobs.daily import build_digest, run_daily

SETTINGS = Settings()


def setup_loans(s):
    suresh = svc.create_party(s, "Suresh")
    ravi = svc.create_party(s, "Ravi")
    taken = svc.create_loan(s, party_id=suresh.id, direction="TAKEN", book="BUSINESS", principal_paise=10_000_000,
                            start_date=D(2026, 1, 1), rate=Decimal("1.5"), due_day=5, as_of=D(2026, 1, 1))
    given = svc.create_loan(s, party_id=ravi.id, direction="GIVEN", book="PERSONAL", principal_paise=10_000_000,
                            start_date=D(2026, 1, 1), rate=Decimal("2.5"), due_day=10, as_of=D(2026, 1, 1))
    svc.link_funding(s, taken_loan_id=taken.id, given_loan_id=given.id, amount_paise=10_000_000,
                     link_date=D(2026, 1, 1), confirm_cross_book=True)
    return taken, given


def test_three_days_before_reminders(s):
    setup_loans(s)
    dg = build_digest(s, D(2026, 1, 2), SETTINGS)
    text = dg.text()
    assert "[BIZ] Pay ₹193.55 interest to Suresh on 05 Jan 2026" in text
    assert [i.kind for i in dg.items] == ["REMINDER"]  # Ravi's 10 Jan is 8 days away
    assert "You pay Suresh on 05 Jan 2026 but Ravi pays you on 10 Jan 2026" in text


def test_pay_today_and_overdue_never_muted(s):
    setup_loans(s)
    muted = replace(SETTINGS, mute_books=frozenset({"BUSINESS", "PERSONAL"}))
    today = build_digest(s, D(2026, 1, 5), muted)
    assert [i.kind for i in today.pay] == ["PAY_ALERT"] and "TODAY" in today.pay[0].text
    late = build_digest(s, D(2026, 1, 8), muted)
    assert "you owe Suresh ₹193.55, 3 days late" in late.pay[0].text
    assert late.collect == []  # muted, and Ravi isn't due yet


def test_borrower_overdue_line(s):
    setup_loans(s)
    dg = build_digest(s, D(2026, 1, 20), SETTINGS)
    assert [i.text for i in dg.collect if i.kind == "OVERDUE"] == ["Ravi owes ₹725.81 overdue, oldest 7 days late"]


def test_run_is_idempotent_and_logged(s):
    setup_loans(s)
    sent = []
    senders = {"TELEGRAM": lambda subject, text: sent.append(text)}
    run_daily(s, D(2026, 1, 8), SETTINGS, senders)
    run_daily(s, D(2026, 1, 8), SETTINGS, senders)
    assert len(sent) == 1
    assert s.scalar(select(func.count()).select_from(m.ReminderLog)) == 1
    runs = s.scalars(select(m.JobRun).order_by(m.JobRun.id)).all()
    assert [r.ok for r in runs] == [True, True] and "nothing new" in runs[1].summary


def test_send_failure_retried_then_recorded(s):
    setup_loans(s)
    calls = []
    run = run_daily(s, D(2026, 1, 8), SETTINGS, {"EMAIL": lambda subj, text: calls.append(1) or "smtp down"})
    assert len(calls) == 2 and run.ok is False and "smtp down" in run.summary
    assert s.scalar(select(func.count()).select_from(m.ReminderLog)) == 0


def test_follow_up_section(s):
    _, given = setup_loans(s)
    svc.log_contact(s, given.id, actor="o", as_of=D(2026, 1, 20), outcome="PROMISED",
                    promise_amount_paise=322_581, promise_date=D(2026, 1, 22), next_follow_up=D(2026, 1, 22),
                    note="salary on 22nd")
    text = build_digest(s, D(2026, 1, 22), SETTINGS).text()
    assert "FOLLOW UP" in text
    assert "[PER] Promise due today: Ravi ₹3,225.81" in text
    assert "[PER] Follow up: Ravi (Follow-up today · last: salary on 22nd)" in text
    text = build_digest(s, D(2026, 1, 23), SETTINGS).text()
    assert "[PER] Broken promise: Ravi owed ₹3,225.81 by 22 Jan 2026, ₹3,225.81 short" in text
    muted = replace(SETTINGS, mute_books=frozenset({"PERSONAL"}))
    assert "FOLLOW UP" not in build_digest(s, D(2026, 1, 23), muted).text()
