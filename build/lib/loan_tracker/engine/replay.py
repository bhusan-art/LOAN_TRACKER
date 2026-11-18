"""Chronological replay of one loan (engine.md §3.1 + §3.2).

Everything the engine knows about a loan comes from replaying its LIVE
ledger entries in (entry_date, entry_id) order up to as_of:

  for each day d that has entries (and finally as_of):
      1. realize every due whose boundary b <= d (interest over [prev, b-1];
         nothing dated >= b can change it, so dues never change later)
      2. apply the entries dated d, in entry_id order

PAYMENT waterfall (§3.2, DECISIONS #11, #13):
  1. penalties, oldest first
  2. realized interest dues, oldest due_date first
  3. payoff rule: if what is left would clear the principal, the open
     period [prev, d-1] is first realized as a CLOSING DUE (due_date = d)
     and paid from the pool, so a payoff charges interest up to the
     repayment day; any principal still left keeps accruing from d
  4. principal
  5. credit: any surplus (no allocation row; never offsets a future due)

Reversed entries are simply absent, so a reversal recomputes everything
after it (review 2.2). Entries dated after as_of are ignored.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from loan_tracker.engine.accrual import (
    ONE_DAY,
    PrincipalTimeline,
    live_entries,
    net_rate,
    period_segments,
    segments_total,
)
from loan_tracker.engine.calendar import iter_boundaries
from loan_tracker.engine.money import round_half_up
from loan_tracker.engine.types import (
    Allocation,
    Due,
    EngineError,
    LedgerEntry,
    LoanTerms,
    ProjectedDue,
    Segment,
)


@dataclass
class DueState:
    due: Due
    paid: int = 0
    waived: int = 0
    penalty: int = 0
    penalty_paid: int = 0

    @property
    def interest_outstanding(self) -> int:
        return max(0, self.due.interest_paise - self.paid - self.waived)

    @property
    def penalty_outstanding(self) -> int:
        return max(0, self.penalty - self.penalty_paid)


@dataclass
class ReplayResult:
    dues: list[DueState]
    allocations: list[Allocation]
    principal_paise: int
    credit_paise: int
    unattached_penalty_paise: int        # PENALTY posted before any due existed
    open_period_paise: int
    projected_due: ProjectedDue | None
    segments: list[Segment]
    disbursed: bool


@dataclass
class _Run:
    terms: LoanTerms
    rates: list
    boundaries: object
    next_b: date
    prev: date
    principal: int = 0
    credit: int = 0
    loose_penalty: int = 0
    loose_penalty_paid: int = 0
    disbursed: bool = False
    tl: PrincipalTimeline = field(default_factory=list)
    dues: list[DueState] = field(default_factory=list)
    allocations: list[Allocation] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)

    # ---- due realization -------------------------------------------------
    def _realize(self, due_date: date, frame_b: date) -> DueState | None:
        """Realize the period [prev, due_date - 1]; a zero-interest period
        (no principal, or 0% rate) produces no due."""
        start = self.prev
        segs = period_segments(self.terms, self.rates, self.tl, start, due_date - ONE_DAY, frame_b)
        self.prev = due_date
        interest = round_half_up(segments_total(segs))  # §4.1-a generation point
        if interest == 0:
            return None
        self.segments.extend(segs)
        ds = DueState(Due(self.terms.loan_id, start, due_date - ONE_DAY, due_date, interest))
        self.dues.append(ds)
        return ds

    def realize_through(self, d: date) -> None:
        while self.next_b <= d:
            b = self.next_b
            self._realize(b, b)
            self.next_b = next(self.boundaries)

    # ---- money ------------------------------------------------------------
    def _set_principal(self, d: date, p: int) -> None:
        self.principal = p
        if self.tl and self.tl[-1][0] == d:
            self.tl[-1] = (d, p)
        else:
            self.tl.append((d, p))

    def _alloc(self, e: LedgerEntry, period_start: date | None, bucket: str, amount: int) -> None:
        if amount > 0:
            self.allocations.append(Allocation(e.entry_id, period_start, bucket, amount))

    def payment(self, e: LedgerEntry) -> None:
        pool = e.amount_paise
        # 1. penalties, oldest first
        if self.loose_penalty > self.loose_penalty_paid:
            take = min(pool, self.loose_penalty - self.loose_penalty_paid)
            self.loose_penalty_paid += take
            pool -= take
            self._alloc(e, None, "PENALTY", take)
        for ds in self.dues:
            take = min(pool, ds.penalty_outstanding)
            ds.penalty_paid += take
            pool -= take
            self._alloc(e, ds.due.period_start, "PENALTY", take)
        # 2. interest dues, oldest first
        for ds in self.dues:
            take = min(pool, ds.interest_outstanding)
            ds.paid += take
            pool -= take
            self._alloc(e, ds.due.period_start, "INTEREST", take)
        # 3. payoff rule: realize the open period as a closing due
        if self.principal > 0 and pool >= self.principal and self.prev < e.entry_date:
            closing = self._realize(e.entry_date, self.next_b)
            if closing is not None:
                take = min(pool, closing.interest_outstanding)
                closing.paid += take
                pool -= take
                self._alloc(e, closing.due.period_start, "INTEREST", take)
        # 4. principal
        take = min(pool, self.principal)
        if take:
            self._set_principal(e.entry_date, self.principal - take)
            pool -= take
            self._alloc(e, None, "PRINCIPAL", take)
        # 5. credit (no allocation row, review 2.6)
        self.credit += pool

    def waiver(self, e: LedgerEntry) -> None:
        if e.amount_paise is None:  # waive the oldest unsettled due in full
            for ds in self.dues:
                if ds.interest_outstanding > 0:
                    ds.waived += ds.interest_outstanding
                    return
            return
        left = e.amount_paise
        for ds in self.dues:  # oldest first; any excess is ignored
            take = min(left, ds.interest_outstanding)
            ds.waived += take
            left -= take

    def penalty(self, e: LedgerEntry) -> None:
        if self.dues:
            self.dues[-1].penalty += e.amount_paise
        else:
            self.loose_penalty += e.amount_paise

    def apply(self, e: LedgerEntry) -> None:
        t = e.type
        if t in ("DISBURSE", "TOPUP", "PAYMENT", "PENALTY") and (e.amount_paise or 0) <= 0:
            raise EngineError(f"entry {e.entry_id}: {t} needs a positive amount_paise")
        if t in ("DISBURSE", "TOPUP"):
            self.disbursed = True
            self._set_principal(e.entry_date, self.principal + e.amount_paise)
        elif t == "PAYMENT":
            self.payment(e)
        elif t == "WAIVER":
            self.waiver(e)
        elif t == "PENALTY":
            self.penalty(e)
        elif t == "RATE_CHANGE":
            if e.new_rate is None or e.new_rate < 0:
                raise EngineError(f"entry {e.entry_id}: RATE_CHANGE needs new_rate >= 0")
        elif t == "CAPITALISE":
            raise NotImplementedError("CAPITALISE is a v2 feature (DECISIONS #2)")
        else:  # pragma: no cover
            raise EngineError(f"entry {e.entry_id}: unknown type {t}")


def replay(terms: LoanTerms, entries: Sequence[LedgerEntry], as_of: date) -> ReplayResult:
    if terms.interest_type != "SIMPLE":
        raise NotImplementedError(f"v1 supports SIMPLE interest only (DECISIONS #1), got {terms.interest_type}")
    live = [e for e in live_entries(entries) if e.entry_date <= as_of]
    for e in live:
        if e.entry_date < terms.start_date:
            raise EngineError(f"entry {e.entry_id} is dated before the loan start {terms.start_date}")
    boundaries = iter_boundaries(terms)
    run = _Run(
        terms=terms,
        rates=net_rate(terms, live),
        boundaries=boundaries,
        next_b=next(boundaries),
        prev=terms.start_date,
    )
    for e in live:
        run.realize_through(e.entry_date)
        run.apply(e)
    run.realize_through(as_of)

    open_paise = 0
    projected = None
    if run.principal > 0:
        open_segs = period_segments(terms, run.rates, run.tl, run.prev, as_of - ONE_DAY, run.next_b)
        open_paise = round_half_up(segments_total(open_segs))  # §4.1-b
        full = period_segments(terms, run.rates, run.tl, run.prev, run.next_b - ONE_DAY, run.next_b)
        projected = ProjectedDue(
            loan_id=terms.loan_id,
            period_start=run.prev,
            period_end=run.next_b - ONE_DAY,
            due_date=run.next_b,
            interest_paise=round_half_up(segments_total(full)),
        )
    return ReplayResult(
        dues=run.dues,
        allocations=run.allocations,
        principal_paise=run.principal,
        credit_paise=run.credit,
        unattached_penalty_paise=max(0, run.loose_penalty - run.loose_penalty_paid),
        open_period_paise=open_paise,
        projected_due=projected,
        segments=run.segments,
        disbursed=run.disbursed,
    )
