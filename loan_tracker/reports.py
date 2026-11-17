"""Dashboard metrics, cash calendar and reports (docs/06-features.md).

Everything is derived from engine results at an explicit as_of date and
honours the book filter (None = consolidated).
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from loan_tracker import engine as eng
from loan_tracker.services import LoanRow, compute_loan, funding_links, portfolio, to_terms


def fy_start(d: date) -> date:
    """Indian financial year starts 1 April."""
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def fy_label(d: date) -> str:
    s = fy_start(d)
    return f"FY {s.year}-{str(s.year + 1)[2:]}"


def _interest_due_between(rows: list[LoanRow], direction: str, start: date, end: date) -> int:
    return sum(d.due.interest_paise for r in rows if r.loan.direction == direction
               for d in r.result.dues if start <= d.due.due_date <= end)


def _received_between(rows: list[LoanRow], direction: str, start: date, end: date) -> int:
    total = 0
    for r in rows:
        if r.loan.direction != direction:
            continue
        dates = {e.id: e.entry_date for e in r.loan.entries}
        total += sum(a.amount_paise for a in r.result.allocations
                     if a.bucket == "INTEREST" and start <= dates[a.entry_id] <= end)
    return total


@dataclass
class TodayItem:
    loan_id: int
    party: str
    book: str
    direction: str
    due_date: date
    amount_paise: int
    status: str
    overdue_days: int = 0
    count: int = 1


@dataclass
class Dashboard:
    as_of: date
    book: str | None
    lent_out_paise: int = 0
    borrowed_paise: int = 0
    own_capital_deployed_paise: int = 0
    interest_earned: dict = field(default_factory=dict)
    interest_cost: dict = field(default_factory=dict)
    net_interest_income: dict = field(default_factory=dict)
    collected_paise: int = 0
    pending_paise: int = 0
    overdue_count: int = 0
    overdue_paise: int = 0
    aging: dict = field(default_factory=dict)
    effective_yield_bps: int = 0
    idle_cost_paise_per_cycle: int = 0
    concentration: list = field(default_factory=list)
    pay_soon: list[TodayItem] = field(default_factory=list)
    collect_soon: list[TodayItem] = field(default_factory=list)
    warnings: list[eng.MismatchFlag] = field(default_factory=list)
    warning_text: list[str] = field(default_factory=list)
    negative_spreads: list = field(default_factory=list)
    active_loans: int = 0
    trend: list = field(default_factory=list)          # [(label, earned, cost)] last 12 months
    status_counts: dict = field(default_factory=dict)  # loan status -> count


def dashboard(s: Session, as_of: date, book: str | None = None, *, soon_days: int = 7,
              concentration_flag_pct: int = 25) -> Dashboard:
    rows = portfolio(s, as_of, book=book)
    db = Dashboard(as_of=as_of, book=book)
    given = [r for r in rows if r.loan.direction == "GIVEN"]
    taken = [r for r in rows if r.loan.direction == "TAKEN"]
    db.active_loans = sum(1 for r in rows if r.status not in ("CLOSED", "SETTLED", "WRITTEN_OFF"))
    db.lent_out_paise = sum(r.result.balances.principal_outstanding_paise for r in given)
    db.borrowed_paise = sum(r.result.balances.principal_outstanding_paise for r in taken)

    links = [l for l in funding_links(s)]
    given_ids = {r.loan.id for r in given}
    funded = sum(l.amount_paise for l in links if l.given_loan_id in given_ids)
    db.own_capital_deployed_paise = max(0, db.lent_out_paise - funded)

    month_start = as_of.replace(day=1)
    periods = {"month": (month_start, as_of), "fy": (fy_start(as_of), as_of), "lifetime": (date.min, as_of)}
    for key, (a, b) in periods.items():
        db.interest_earned[key] = _interest_due_between(rows, "GIVEN", a, b)
        db.interest_cost[key] = _interest_due_between(rows, "TAKEN", a, b)
        db.net_interest_income[key] = db.interest_earned[key] - db.interest_cost[key]

    # 12-month interest trend (by due month), for the overview chart
    first = (as_of.replace(day=1) - timedelta(days=330)).replace(day=1)
    months = []
    m = first
    while len(months) < 12:
        months.append(m)
        m = (m + timedelta(days=32)).replace(day=1)
    for i, ms in enumerate(months):
        me = (months[i + 1] - timedelta(days=1)) if i + 1 < len(months) else as_of
        db.trend.append((ms.strftime("%b"), _interest_due_between(rows, "GIVEN", ms, me),
                         _interest_due_between(rows, "TAKEN", ms, me)))
    for r in rows:
        db.status_counts[r.status] = db.status_counts.get(r.status, 0) + 1

    db.collected_paise = sum(r.result.balances.interest_paid_paise for r in given)
    db.pending_paise = sum(r.result.balances.interest_outstanding_paise for r in given)

    db.aging = {"0_30": 0, "31_60": 0, "61_90": 0, "90_plus": 0}
    for r in rows:
        for d in r.result.dues:
            if d.status == "OVERDUE":
                db.overdue_count += 1
                db.overdue_paise += d.outstanding_paise
                db.aging[eng.aging_bucket(d.overdue_days)] += d.outstanding_paise

    received_year = _received_between(rows, "GIVEN", as_of - timedelta(days=365), as_of)
    if db.lent_out_paise:
        db.effective_yield_bps = received_year * 10_000 // db.lent_out_paise

    # concentration: exposure per borrower as a share of total lent out
    by_party: dict[str, int] = defaultdict(int)
    for r in given:
        by_party[r.loan.party.name] += r.result.balances.principal_outstanding_paise
    if db.lent_out_paise:
        db.concentration = sorted(
            ((name, amt, amt * 100 // db.lent_out_paise, amt * 100 > db.lent_out_paise * concentration_flag_pct)
             for name, amt in by_party.items() if amt),
            key=lambda x: -x[1])

    horizon = as_of + timedelta(days=soon_days)
    for r in rows:
        target = db.pay_soon if r.loan.direction == "TAKEN" else db.collect_soon
        unpaid = [d for d in r.result.dues if d.outstanding_paise > 0]
        if unpaid:  # one line per loan: total still owed on past dues, oldest date
            worst = "OVERDUE" if any(d.status == "OVERDUE" for d in unpaid) else unpaid[0].status
            target.append(TodayItem(r.loan.id, r.loan.party.name, r.loan.book, r.loan.direction,
                                    unpaid[0].due.due_date, sum(d.outstanding_paise for d in unpaid),
                                    worst, max(d.overdue_days for d in unpaid), len(unpaid)))
        p = r.result.projected_due
        if p and p.due_date <= horizon and r.status not in ("SETTLED", "WRITTEN_OFF"):
            target.append(TodayItem(r.loan.id, r.loan.party.name, r.loan.book, r.loan.direction,
                                    p.due_date, p.interest_paise, "UPCOMING"))
    for lst in (db.pay_soon, db.collect_soon):
        lst.sort(key=lambda i: (i.due_date, i.party))

    terms = {r.loan.id: to_terms(r.loan) for r in rows}
    shown_links = [eng.FundingLink(l.taken_loan_id, l.given_loan_id, l.amount_paise, l.link_date)
                   for l in links if l.taken_loan_id in terms and l.given_loan_id in terms]
    taken_principal = {r.loan.id: r.result.balances.principal_outstanding_paise for r in taken}
    rep = eng.spread(shown_links, terms, taken_principal)
    db.idle_cost_paise_per_cycle = rep.idle_cost_paise_per_cycle
    db.negative_spreads = list(rep.negative_spread)
    results = {r.loan.id: r.result for r in rows}
    written_off = frozenset(r.loan.id for r in rows if r.status == "WRITTEN_OFF")
    db.warnings = eng.mismatch_check(shown_links, results, written_off)
    names = {r.loan.id: r.loan.party.name for r in rows}
    db.warning_text = [describe_flag(f, names) for f in db.warnings]
    db.warning_text += [
        f"Negative spread: {names[sp.link.given_loan_id]}'s loan earns {_inr(-sp.spread_paise_per_cycle)} a month "
        f"less than borrowing from {names[sp.link.taken_loan_id]} costs." for sp in db.negative_spreads]
    return db


def _inr(paise: int) -> str:
    from loan_tracker.web.formatting import inr
    return inr(paise)


def describe_flag(f: eng.MismatchFlag, names: dict[int, str]) -> str:
    """Owner-facing wording for an engine mismatch flag."""
    from loan_tracker.web.formatting import dmy
    lender, borrower = names.get(f.taken_loan_id, f"loan {f.taken_loan_id}"), names.get(f.given_loan_id, "")
    if f.kind == "WARNING":
        return (f"You pay {lender} on {dmy(f.pay_date)} but {borrower} pays you on {dmy(f.receive_date)}. "
                f"Consider moving {borrower}'s due day a few days earlier.")
    if f.kind == "RISK":
        return (f"{borrower} is overdue, so your next {_inr(f.amount_paise or 0)} to {lender} "
                f"must come from your own money.")
    return f"{borrower}'s loan is written off, but you still owe {lender} {_inr(f.amount_paise or 0)}."


@dataclass
class CalendarDay:
    day: date
    money_in: list[TodayItem] = field(default_factory=list)
    money_out: list[TodayItem] = field(default_factory=list)
    net_paise: int = 0
    running_paise: int = 0


def cash_calendar(s: Session, as_of: date, days: int = 30, book: str | None = None) -> list[CalendarDay]:
    """Expected interest (and principal at maturity) in vs out per day.

    Future dues come from the engine itself: computing each loan at the
    horizon with no future payments gives exactly the dues that will fall due.
    Amounts already overdue are shown on today. Running balance starts at 0
    (the app does not know your bank balance)."""
    horizon = as_of + timedelta(days=days)
    out: dict[date, CalendarDay] = {}

    def add(d: date, item: TodayItem, incoming: bool):
        day = out.setdefault(d, CalendarDay(d))
        (day.money_in if incoming else day.money_out).append(item)

    for row in portfolio(s, as_of, book=book):
        loan = row.loan
        if row.status in ("SETTLED", "WRITTEN_OFF", "CLOSED"):
            continue
        incoming = loan.direction == "GIVEN"
        unpaid = [d for d in row.result.dues if d.outstanding_paise > 0]
        if unpaid:  # arrears: one line per loan, shown on today
            add(as_of, TodayItem(loan.id, loan.party.name, loan.book, loan.direction,
                                 unpaid[0].due.due_date, sum(d.outstanding_paise for d in unpaid),
                                 "OVERDUE" if any(d.status == "OVERDUE" for d in unpaid) else "PARTIAL",
                                 max(d.overdue_days for d in unpaid), len(unpaid)), incoming)
        future = compute_loan(loan, horizon)
        for d in future.dues:
            if as_of < d.due.due_date <= horizon:
                add(d.due.due_date, TodayItem(loan.id, loan.party.name, loan.book, loan.direction,
                                              d.due.due_date, d.due.interest_paise, "UPCOMING"), incoming)
        mat = loan.maturity_date
        if mat and as_of < mat <= horizon and row.result.balances.principal_outstanding_paise:
            add(mat, TodayItem(loan.id, loan.party.name, loan.book, loan.direction, mat,
                               row.result.balances.principal_outstanding_paise, "PRINCIPAL"), incoming)
    running = 0
    for d in sorted(out):
        day = out[d]
        day.net_paise = sum(i.amount_paise for i in day.money_in) - sum(i.amount_paise for i in day.money_out)
        running += day.net_paise
        day.running_paise = running
    return [out[d] for d in sorted(out)]


def fy_interest_report(s: Session, as_of: date) -> list[dict]:
    """Interest earned / paid / net per book per financial year (Apr-Mar)."""
    rows = portfolio(s, as_of)
    acc: dict[tuple[str, str], dict] = {}
    for r in rows:
        for d in r.result.dues:
            key = (fy_label(d.due.due_date), r.loan.book)
            rec = acc.setdefault(key, {"fy": key[0], "book": key[1], "earned": 0, "paid": 0,
                                       "received": 0, "paid_out": 0})
            if r.loan.direction == "GIVEN":
                rec["earned"] += d.due.interest_paise
                rec["received"] += d.paid_paise
            else:
                rec["paid"] += d.due.interest_paise
                rec["paid_out"] += d.paid_paise
    for rec in acc.values():
        rec["net"] = rec["earned"] - rec["paid"]
    return sorted(acc.values(), key=lambda x: (x["fy"], x["book"]), reverse=True)


def overdue_report(s: Session, as_of: date, book: str | None = None) -> list[TodayItem]:
    items = []
    for r in portfolio(s, as_of, book=book):
        for d in r.result.dues:
            if d.status == "OVERDUE":
                items.append(TodayItem(r.loan.id, r.loan.party.name, r.loan.book, r.loan.direction,
                                       d.due.due_date, d.outstanding_paise, d.status, d.overdue_days))
    return sorted(items, key=lambda i: -i.overdue_days)


def cross_book_report(s: Session, as_of: date) -> list[dict]:
    rows = {r.loan.id: r for r in portfolio(s, as_of)}
    out = []
    for l in funding_links(s):
        t, g = rows[l.taken_loan_id].loan, rows[l.given_loan_id].loan
        if t.book != g.book:
            out.append({"link": l, "taken": t, "given": g})
    return out


def export_csv(s: Session, as_of: date, book: str | None = None) -> str:
    """Every ledger entry with its allocation split, one row per allocation."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["loan_id", "party", "direction", "book", "entry_id", "entry_date", "type",
                "amount_rupees", "bucket", "allocated_rupees", "due_period_start", "reverses", "note"])
    for r in portfolio(s, as_of, book=book):
        allocs = defaultdict(list)
        for a in r.result.allocations:
            allocs[a.entry_id].append(a)
        for e in r.loan.entries:
            base = [r.loan.id, _cell(r.loan.party.name), r.loan.direction, r.loan.book, e.id,
                    e.entry_date.isoformat(), e.type, rupees(e.amount_paise)]
            tail = [e.reverses_id or "", _cell(e.note)]
            if allocs.get(e.id):
                for a in allocs[e.id]:
                    w.writerow(base + [a.bucket, rupees(a.amount_paise),
                                       a.due_period_start.isoformat() if a.due_period_start else ""] + tail)
            else:
                w.writerow(base + ["", "", ""] + tail)
    return buf.getvalue()


def _cell(text: str | None) -> str:
    """Text cell safe to open in Excel: a leading = + - @ would run as a formula."""
    t = text or ""
    return "'" + t if t[:1] in ("=", "+", "-", "@", "\t", "\r") else t


def rupees(paise: int | None) -> str:
    """10_000_050 -> '100000.50' (plain, for CSV)."""
    if paise is None:
        return ""
    sign = "-" if paise < 0 else ""
    return f"{sign}{abs(paise) // 100}.{abs(paise) % 100:02d}"


def calendar_weeks(cal: list[CalendarDay], start: date, end: date) -> list[list[tuple[date, CalendarDay | None, bool]]]:
    """Sunday-first weeks covering [start, end] for the month-grid view.
    Each cell: (day, CalendarDay or None, in_window)."""
    by_day = {d.day: d for d in cal}
    first = start - timedelta(days=(start.weekday() + 1) % 7)
    last = end + timedelta(days=(5 - end.weekday()) % 7)
    weeks, week, d = [], [], first
    while d <= last:
        week.append((d, by_day.get(d), start <= d <= end))
        if len(week) == 7:
            weeks.append(week)
            week = []
        d += timedelta(days=1)
    return weeks
