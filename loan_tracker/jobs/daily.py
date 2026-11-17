"""The daily job (docs/design/api.md §2, 06-features notifications).

Run once a day (cron, ~08:00 IST):
    python -m loan_tracker.cli run-job

Steps, each safe to re-run for the same date:
  1. recompute every loan (refreshes due/allocation caches and loan status)
  2. build ONE digest: pay -> collect -> warnings, each line tagged [BIZ]/[PER]
  3. send it on every configured channel, skipping lines already sent today
     on that channel (reminder_log UNIQUE (loan_id, due_date, kind, channel, sent_on))
  4. record the run in job_run (the dashboard shows the last run)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from loan_tracker import reports, services as svc
from loan_tracker.config import Settings
from loan_tracker.db import models as m
from loan_tracker.web.formatting import dmy, inr


@dataclass
class DigestItem:
    loan_id: int
    due_date: date
    kind: str            # REMINDER / OVERDUE / PAY_ALERT (reminder_kind enum)
    direction: str
    book: str
    text: str

    @property
    def tag(self) -> str:
        return "[BIZ]" if self.book == "BUSINESS" else "[PER]"


@dataclass
class Digest:
    run_date: date
    pay: list[DigestItem] = field(default_factory=list)
    collect: list[DigestItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    follow_up: list[str] = field(default_factory=list)   # collections (Phase 9)
    summary: list[str] = field(default_factory=list)

    @property
    def items(self) -> list[DigestItem]:
        return self.pay + self.collect

    def text(self, items: list[DigestItem] | None = None) -> str:
        items = self.items if items is None else items
        pay = [i for i in items if i.direction == "TAKEN"]
        collect = [i for i in items if i.direction == "GIVEN"]
        lines = [f"Loan Tracker · {dmy(self.run_date)}"]
        if pay:
            lines += ["", "TO PAY"] + [f"{i.tag} {i.text}" for i in pay]
        if collect:
            lines += ["", "TO COLLECT"] + [f"{i.tag} {i.text}" for i in collect]
        if self.follow_up:
            lines += ["", "FOLLOW UP"] + self.follow_up
        if self.warnings:
            lines += ["", "WARNINGS"] + self.warnings
        if self.summary:
            lines += [""] + self.summary
        return "\n".join(lines)


def build_digest(s: Session, run_date: date, settings: Settings) -> Digest:
    dg = Digest(run_date)
    remind_on = run_date + timedelta(days=settings.reminder_days_before)
    rows = svc.portfolio(s, run_date)
    for r in rows:
        loan, res = r.loan, r.result
        if r.status in ("SETTLED", "WRITTEN_OFF", "CLOSED"):
            continue
        who, taken = loan.party.name, loan.direction == "TAKEN"
        muted = loan.book in settings.mute_books
        target = dg.pay if taken else dg.collect

        def add(due_date, kind, text, never_mute=False):
            if muted and not never_mute:
                return
            target.append(DigestItem(loan.id, due_date, kind, loan.direction, loan.book, text))

        unpaid = [d for d in res.dues if d.outstanding_paise > 0]
        if taken:
            due_today = [d for d in unpaid if d.due.due_date == run_date]
            late = [d for d in unpaid if d.due.due_date < run_date]
            if due_today:
                add(run_date, "PAY_ALERT", f"Pay {inr(due_today[0].outstanding_paise)} interest to {who} TODAY",
                    never_mute=True)
            if late:
                days = (run_date - late[0].due.due_date).days
                add(late[0].due.due_date, "PAY_ALERT",
                    f"OVERDUE: you owe {who} {inr(sum(d.outstanding_paise for d in late))}"
                    f"{f' ({len(late)} dues)' if len(late) > 1 else ''}, {days} day{'s' if days != 1 else ''} late",
                    never_mute=True)
        else:
            late = [d for d in unpaid if d.status == "OVERDUE"]
            if late:
                add(late[0].due.due_date, "OVERDUE",
                    f"{who} owes {inr(sum(d.outstanding_paise for d in late))} overdue"
                    f"{f' ({len(late)} dues)' if len(late) > 1 else ''}, oldest {late[0].overdue_days} days late")
        p = res.projected_due
        if p and p.due_date == remind_on:
            verb = f"Pay {inr(p.interest_paise)} interest to {who}" if taken else f"{who}'s {inr(p.interest_paise)} is due"
            add(p.due_date, "REMINDER", f"{verb} on {dmy(p.due_date)}")
        mat = loan.maturity_date
        if mat and (mat - run_date).days in (30, 7, 1) and res.balances.principal_outstanding_paise:
            add(mat, "REMINDER", f"Loan {'from' if taken else 'to'} {who} matures {dmy(mat)}: "
                                 f"{inr(res.balances.principal_outstanding_paise)} principal due",
                never_mute=taken)

    ov = reports.collections_overview(s, run_date)
    tag = lambda i: "[BIZ]" if i.book == "BUSINESS" else "[PER]"
    live = lambda i: i.book not in settings.mute_books
    dg.follow_up += [f"{tag(i)} Broken promise: {i.party} owed {inr(i.amount_paise)} by {dmy(i.on_date)}, "
                     f"{inr(i.short_paise)} short" for i in ov.broken if live(i)]
    dg.follow_up += [f"{tag(i)} Promise due today: {i.party} {inr(i.short_paise)}"
                     for i in ov.promises_due if live(i) and i.on_date == run_date]
    dg.follow_up += [f"{tag(i)} Follow up: {i.party} ({i.detail})" for i in ov.follow_ups if live(i)]

    db = reports.dashboard(s, run_date)
    dg.warnings = list(db.warning_text)
    if run_date.day == 1:
        last_month_end = run_date - timedelta(days=1)
        last_month = last_month_end.replace(day=1)
        for book in ("BUSINESS", "PERSONAL"):
            rows_b = [r for r in rows if r.loan.book == book]
            earned = sum(d.due.interest_paise for r in rows_b if r.loan.direction == "GIVEN"
                         for d in r.result.dues if last_month <= d.due.due_date <= last_month_end)
            cost = sum(d.due.interest_paise for r in rows_b if r.loan.direction == "TAKEN"
                       for d in r.result.dues if last_month <= d.due.due_date <= last_month_end)
            if earned or cost:
                dg.summary.append(f"{last_month:%B}: {book.lower()} net interest {inr(earned - cost)} "
                                  f"(earned {inr(earned)}, cost {inr(cost)})")
        cal = reports.cash_calendar(s, run_date, 30)
        if cal:
            ins = sum(i.amount_paise for d in cal for i in d.money_in)
            outs = sum(i.amount_paise for d in cal for i in d.money_out)
            dg.summary.append(f"Next 30 days: in {inr(ins)}, out {inr(outs)}")
    return dg


Sender = Callable[[str, str], "str | None"]


def run_daily(s: Session, run_date: date, settings: Settings, senders: dict[str, Sender]) -> m.JobRun:
    """Run all steps for run_date inside the caller's transaction (the CLI
    records a failed run separately if this raises)."""
    run = m.JobRun(run_date=run_date)
    s.add(run)
    s.flush()
    notes = [f"recomputed {svc.recompute_all(s, run_date)} loans"]
    dg = build_digest(s, run_date, settings)
    if not senders:
        notes.append("no notification channel configured")
    failed = False
    for channel, send in senders.items():
        already = {(r.loan_id, r.due_date, r.kind) for r in s.scalars(
            select(m.ReminderLog).where(m.ReminderLog.channel == channel, m.ReminderLog.sent_on == run_date))}
        sent_today = bool(already) or any(
            f"{channel}: sent" in (jr.summary or "") for jr in s.scalars(
                select(m.JobRun).where(m.JobRun.run_date == run_date, m.JobRun.ok.is_(True))))
        fresh = [i for i in dg.items if (i.loan_id, i.due_date, i.kind) not in already]
        if not fresh and (sent_today or not (dg.warnings or dg.summary or dg.follow_up)):
            notes.append(f"{channel}: nothing new")
            continue
        text = dg.text(fresh)
        subject = f"Loan Tracker {run_date:%d %b}"
        err = send(subject, text)
        if err:  # one retry, then report on the dashboard
            err = send(subject, text)
        if err:
            failed = True
            notes.append(f"{channel}: {err}")
            continue
        for key in dict.fromkeys((i.loan_id, i.due_date, i.kind) for i in fresh):
            s.add(m.ReminderLog(loan_id=key[0], due_date=key[1], kind=key[2], channel=channel, sent_on=run_date))
        notes.append(f"{channel}: sent {len(fresh)} lines")
    run.ok = not failed
    run.finished_at = datetime.now(timezone.utc)
    run.summary = "; ".join(notes)
    s.flush()
    return run
