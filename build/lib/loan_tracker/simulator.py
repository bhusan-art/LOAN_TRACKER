"""What-if simulator (Phase 12): the real engine on copies, nothing saved.

Pure: no database, no clock. reports/web load a loan's terms and entries and
pass them in. The real entries are never changed; scenario and assumed
payments are extra in-memory entries with negative ids.

Assumption, the same for the actual and the simulated path: from today to the
horizon, interest is paid on each due date (and principal at maturity). So the
comparison shows only the effect of the change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import count
from typing import Literal, Sequence

from loan_tracker.engine import compute
from loan_tracker.engine.calendar import _add_months
from loan_tracker.engine.types import LedgerEntry, LoanTerms

Kind = Literal["NONE", "EXTRA_PAYMENT", "PAYOFF", "RATE_CHANGE", "MISS", "TOPUP"]
KINDS = ("EXTRA_PAYMENT", "PAYOFF", "RATE_CHANGE", "MISS", "TOPUP")


@dataclass(frozen=True)
class Scenario:
    kind: Kind
    on: date | None = None
    amount_paise: int = 0          # EXTRA_PAYMENT, TOPUP
    new_rate: Decimal | None = None  # RATE_CHANGE
    months: int = 0                # MISS: how many dues are not paid, from `on`


@dataclass(frozen=True)
class SimDue:
    due_date: date
    interest_paise: int
    paid: bool                     # covered by the end of the window


@dataclass
class Projection:
    dues: list[SimDue] = field(default_factory=list)   # dues falling in (today, horizon]
    interest_paise: int = 0         # interest falling due in the window
    payments_paise: int = 0         # money paid on the loan in the window (assumed + scenario)
    topups_paise: int = 0           # extra principal lent/borrowed in the window
    principal_left_paise: int = 0   # at the horizon
    interest_left_paise: int = 0    # unpaid interest at the horizon
    payoff_paise: int = 0           # to close the loan on the horizon day
    effective_rate: Decimal = Decimal(0)
    closed_on: date | None = None   # principal reached zero in the window


def _validate(sc: Scenario, today: date, horizon: date) -> None:
    if sc.kind == "NONE":
        return
    if sc.kind not in KINDS:
        raise ValueError(f"unknown scenario {sc.kind}")
    if sc.on is None or not today <= sc.on <= horizon:
        raise ValueError(f"the scenario date must be between {today:%d %b %Y} and {horizon:%d %b %Y}")
    if sc.kind in ("EXTRA_PAYMENT", "TOPUP") and sc.amount_paise <= 0:
        raise ValueError("the amount must be more than zero")
    if sc.kind == "RATE_CHANGE" and (sc.new_rate is None or sc.new_rate < 0):
        raise ValueError("enter the new rate")
    if sc.kind == "MISS" and not 1 <= sc.months <= 24:
        raise ValueError("months missed must be 1 to 24")


def project(terms: LoanTerms, entries: Sequence[LedgerEntry], today: date, horizon: date,
            sc: Scenario) -> Projection:
    """Run the loan from today to horizon under scenario sc (NONE = as it is)."""
    _validate(sc, today, horizon)
    ids = count(-1, -1)
    extra: list[LedgerEntry] = []

    def add(kind: str, d: date, amount: int | None = None, new_rate: Decimal | None = None) -> None:
        extra.append(LedgerEntry(next(ids), d, kind, amount, new_rate, "what-if", None))

    if sc.kind == "EXTRA_PAYMENT":
        add("PAYMENT", sc.on, sc.amount_paise)
    elif sc.kind == "TOPUP":
        add("TOPUP", sc.on, sc.amount_paise)
    elif sc.kind == "RATE_CHANGE":
        add("RATE_CHANGE", sc.on, new_rate=sc.new_rate)
    skip_until = _add_months(sc.on, sc.months) if sc.kind == "MISS" else None
    stop_after = sc.on if sc.kind == "PAYOFF" else None

    def skipped(d: date) -> bool:
        return skip_until is not None and sc.on <= d < skip_until

    if stop_after is not None:
        _schedule(terms, entries, extra, ids, today, stop_after - timedelta(days=1), skipped)
        payoff = compute(terms, [*entries, *extra], stop_after).balances.payoff_today_paise
        if payoff:
            add("PAYMENT", stop_after, payoff)
    else:
        _schedule(terms, entries, extra, ids, today, horizon, skipped)

    res = compute(terms, [*entries, *extra], horizon)
    out = Projection()
    for d in res.dues:
        if today < d.due.due_date <= horizon:
            out.dues.append(SimDue(d.due.due_date, d.due.interest_paise, d.outstanding_paise == 0))
            out.interest_paise += d.due.interest_paise
    out.payments_paise = sum(e.amount_paise for e in extra if e.type == "PAYMENT")
    out.topups_paise = sum(e.amount_paise for e in extra if e.type == "TOPUP")
    b = res.balances
    out.principal_left_paise = b.principal_outstanding_paise
    out.interest_left_paise = b.interest_outstanding_paise
    out.payoff_paise = b.payoff_today_paise
    rate = terms.rate
    for e in sorted((*entries, *extra), key=lambda e: e.entry_date):
        if e.type == "RATE_CHANGE" and e.entry_date <= horizon and e.new_rate is not None:
            rate = e.new_rate
    out.effective_rate = rate
    if b.principal_outstanding_paise == 0:
        out.closed_on = stop_after or terms.maturity_date
    return out


def _schedule(terms: LoanTerms, entries: Sequence[LedgerEntry], extra: list[LedgerEntry], ids,
              today: date, until: date, skipped=lambda d: False) -> None:
    """Append the assumed on-time payments to `extra`: each interest due in
    (today, until] on its due date, oldest first and one at a time (a due's
    amount depends on everything before it), then principal at maturity."""
    paid: set[date] = set()
    principal_done = False
    for _ in range(1000):
        res = compute(terms, [*entries, *extra], until)
        due = next((d for d in res.dues if today < d.due.due_date <= until
                    and d.due.due_date not in paid and not skipped(d.due.due_date)), None)
        mat = terms.maturity_date
        if due is not None and (principal_done or not mat or due.due.due_date <= mat):
            paid.add(due.due.due_date)
            extra.append(LedgerEntry(next(ids), due.due.due_date, "PAYMENT", due.due.interest_paise,
                                     None, "what-if", None))
            continue
        if not principal_done and mat and today < mat <= until and not skipped(mat):
            principal_done = True
            left = compute(terms, [*entries, *extra], mat).balances.principal_outstanding_paise
            if left:
                extra.append(LedgerEntry(next(ids), mat, "PAYMENT", left, None, "what-if", None))
            continue
        if due is None:
            return
        paid.add(due.due.due_date)          # due after maturity once principal handling is done
        extra.append(LedgerEntry(next(ids), due.due.due_date, "PAYMENT", due.due.interest_paise,
                                 None, "what-if", None))
    raise RuntimeError("simulation did not settle")


def new_loan_flows(terms: LoanTerms, today: date, horizon: date) -> list[tuple[date, int]]:
    """Cash flows of a loan you have not made yet: the principal on the start
    date, interest on each due date, principal back at maturity (in the window).
    + = money in, - = money out, from your side."""
    disb = LedgerEntry(-1, terms.start_date, "DISBURSE", terms.principal_paise, None, "what-if", None)
    extra: list[LedgerEntry] = []
    _schedule(terms, [disb], extra, count(-2, -1), min(today, terms.start_date), horizon)
    sign = 1 if terms.direction == "GIVEN" else -1
    flows: dict[date, int] = {}
    if today <= terms.start_date <= horizon:
        flows[terms.start_date] = -sign * terms.principal_paise
    for e in extra:
        flows[e.entry_date] = flows.get(e.entry_date, 0) + sign * e.amount_paise
    return sorted(flows.items())
