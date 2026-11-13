"""The pure calculation engine (principle P7).

    compute(loan_terms, ledger_entries, as_of_date) -> {dues, allocations, balances}

No database or I/O inside; the same inputs always give the same output.

Fold model
----------
All *effective* ledger events are processed in date order (ties by
`id`, the stable append order), maintaining one piecewise-constant
timeline of (outstanding principal, rate):

* A DISBURSE / TOPUP / principal-allocation / RATE_CHANGE starts a new
  timeline piece from the event's day (the event day itself accrues at
  the new values — see worked example A, where 15 Mar accrues at
  ₹70,000).
* Interest for a period [b_{k-1}, b_k) between two due boundaries is
  the exact Decimal sum over the pieces inside it, rounded **once** to
  integer paise half-up at the period close (P3).
* PAYMENT is split by the fixed waterfall (P5): penalty (oldest due
  first) → interest (oldest due first) → principal. Anything beyond the
  full payoff is held as a credit, not applied.
* A REVERSAL is processed by *logical exclusion*: the original event is
  dropped from the effective ledger and everything (timeline, dues,
  allocations) is re-derived. The ledger stays append-only (P1/P2);
  derivations are recomputed, never edited.
* WAIVER clears the oldest unpaid interest (or penalty, per the
  entry's target), and a COMPOUND loan re-enters any period interest
  that is still unpaid at a period close into principal (CAPITALISE),
  starting a new timeline piece.

Day-count convention (documented, per spec 6.1): monthly-flat loans
pro-rate `monthly_rate × days / days_in_month`; annual-rate loans use
daily `rate / 365`. Intervals are half-open: the disbursement day
accrues, the due/repayment day is excluded.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal

from ..money import half_up_paise
from .accrual import _interval_month_split
from .accrual import accrue_interval, next_due_day
from .models import (
    EntryType,
    FundingLink,
    InterestType,
    Loan,
    RateUnit,
)

__all__ = [
    "Allocation",
    "Balances",
    "Due",
    "EngineResult",
    "FundingSpread",
    "MismatchWarning",
    "PayoffBreakdown",
    "TimelinePiece",
    "cash_flow_mismatches",
    "compute",
    "funding_spread",
    "predict_dues",
]


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimelinePiece:
    """A half-open interval [start, end) where principal and rate are
    constant. `end` is None while the fold is still open."""

    start: date
    principal_paise: int
    monthly_rate: Decimal
    rate_unit: RateUnit
    end: date | None = None


@dataclass(frozen=True, slots=True)
class Allocation:
    ledger_entry_id: int
    bucket: str            # "PENALTY" | "INTEREST" | "PRINCIPAL" | "CREDIT"
    due_index: int | None  # interest/penalty buckets point at the due
    amount_paise: int


@dataclass(frozen=True, slots=True)
class Due:
    loan_id: int
    index: int               # 1-based cycle number
    period_start: date
    due_date: date           # period end; the due day is excluded from accrual
    interest_paise: int      # rounded at close (P3)
    paid_paise: int
    waived_paise: int
    penalty_paise: int       # penalties posted against this due
    penalty_paid_paise: int
    capitalised_paise: int   # interest moved into principal (COMPOUND)
    status: str              # PAID | WAIVED | PARTIAL | UPCOMING | OVERDUE

    @property
    def unpaid_paise(self) -> int:
        return (self.interest_paise - self.paid_paise - self.waived_paise
                + self.penalty_paise - self.penalty_paid_paise)

    @property
    def late(self) -> bool:
        return self.status == "OVERDUE"


@dataclass(frozen=True, slots=True)
class PayoffBreakdown:
    principal_paise: int
    interest_closed_paise: int
    interest_open_paise: int
    penalty_paise: int
    credit_paise: int
    total_paise: int


@dataclass(frozen=True, slots=True)
class Balances:
    principal_outstanding_paise: int
    interest_accrued_paise: int        # closed periods, rounded
    interest_open_paise: int           # in-flight period, rounded to the paisa
    interest_received_paise: int
    interest_waived_paise: int
    penalty_posted_paise: int
    penalty_paid_paise: int
    credit_paise: int
    payoff_paise: int
    payoff: PayoffBreakdown = field(default_factory=lambda: PayoffBreakdown(0, 0, 0, 0, 0, 0))


@dataclass(frozen=True, slots=True)
class EngineResult:
    loan_id: int
    as_of: date
    balances: Balances
    dues: tuple[Due, ...]
    allocations: tuple[Allocation, ...]
    timeline: tuple[TimelinePiece, ...]
    last_closed_due_date: date | None  # boundary of the open period
    predicted: tuple["PredictedDue", ...] = ()


@dataclass(frozen=True, slots=True)
class PredictedDue:
    """A not-yet-generated cycle, for the cash calendar and reminders."""

    index: int
    due_date: date
    period_start: date
    estimated_paise: int       # constant-principal estimate, rounded


@dataclass(frozen=True, slots=True)
class FundingSpread:
    taken_loan_id: int
    given_loan_id: int
    amount_paise: int
    monthly_spread_paise: int
    idle_paise: int            # taken principal not linked to any given loan

    @property
    def negative(self) -> bool:
        return self.monthly_spread_paise < 0


@dataclass(frozen=True, slots=True)
class MismatchWarning:
    taken_loan_id: int
    given_loan_id: int
    kind: str                 # PAY_BEFORE_RECEIVE | GIVEN_OVERDUE | GIVEN_WRITTEN_OFF
    detail: str


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _reversed_ids(entries: list) -> set[int]:
    """Entries logically removed by REVERSAL events (P2)."""
    out: set[int] = set()
    for e in entries:
        if e.type is EntryType.REVERSAL and e.reference is not None:
            try:
                out.add(int(e.reference))
            except ValueError:
                raise ValueError(
                    f"reversal {e.id} references non-numeric id {e.reference!r}") from None
    return out


def _effective_events(loan: Loan, entries: list) -> list:
    """All effective events in (entry_date, id) order, with the implicit
    opening DISBURSE at the loan's start date if the ledger does not
    carry one."""
    rev = _reversed_ids(entries)
    ev = [e for e in entries if e.id not in rev]
    if not any(e.type is EntryType.DISBURSE for e in ev):
        from .models import LedgerEntry
        ev.append(LedgerEntry(
            id=-1, loan_id=loan.id, entry_date=loan.start_date,
            type=EntryType.DISBURSE, amount_paise=loan.principal_paise,
            note="implicit opening disbursement",
        ))
    ev.sort(key=lambda e: (e.entry_date, e.id))
    return ev


def _new_due(idx: int, loan: Loan, start: date, due_date: date) -> Due:
    return Due(
        loan_id=loan.id, index=idx, period_start=start, due_date=due_date,
        interest_paise=0, paid_paise=0, waived_paise=0, penalty_paise=0,
        penalty_paid_paise=0, capitalised_paise=0, status="UPCOMING",
    )


def _period_interest(piece_starts: list[tuple[date, int, Decimal, RateUnit]],
                     a: date, b: date) -> Decimal:
    """Exact paise for [a, b) given constant (principal, rate) pieces whose
    start dates are all inside [a, b) or before; the last piece runs to b.

    Each piece is clipped to [a, b) and further split by calendar month
    because the monthly pro-ration uses the containing month's length.
    """
    total = Decimal(0)
    starts = sorted(piece_starts)
    boundaries = [starts[0][0]] if starts and starts[0][0] >= a else [a]
    # walk: for each piece start s in [a, b) the piece runs s..next_start;
    # the region [a, first_start) belongs to the piece established before a
    # (represented by first entry whose start < a, or by [a] itself if none).
    regions: list[tuple[date, date, int, Decimal, RateUnit]] = []
    last = a
    items = [s for s in starts if a <= s[0] < b]
    pre = [s for s in starts if s[0] < a]
    if pre:
        p, r, u = pre[-1][1], pre[-1][2], pre[-1][3]
        if items:
            regions.append((a, items[0][0], p, r, u))
        else:
            regions.append((a, b, p, r, u))
    for i, (s, p, r, u) in enumerate(items):
        end = items[i + 1][0] if i + 1 < len(items) else b
        regions.append((s, end, p, r, u))
    for s, e, p, r, u in regions:
        for m1, m2 in _interval_month_split(s, e):
            total += accrue_interval(p, _rate_loan(0, 0, r, u), m1, m2)
    return total


def _rate_loan(rate: Decimal, unit: RateUnit) -> Loan:
    """A loan carrying only the rate data `accrue_interval` needs."""
    return Loan(
        id=0, party_id=0, direction=None,  # type: ignore[arg-type]
        book=None, principal_paise=0, start_date=date(1970, 1, 1),  # type: ignore[arg-type]
        rate=rate, rate_unit=unit, due_day=1,
    )


def _status_for(due: Due, as_of: date, grace: int) -> Due:
    """Recompute a due's status as of `as_of` (spec 6.3)."""
    if due.waived_paise >= due.interest_paise and due.interest_paise > 0 and due.paid_paise == 0:
        return replace(due, status="WAIVED")
    if due.paid_paise >= due.interest_paise and due.interest_paise == due.paid_paise:
        if due.penalty_paise - due.penalty_paid_paise > 0:
            status = "PARTIAL"
        else:
            status = "PAID"
        return replace(due, status=status)
    if due.interest_paise == 0 and due.penalty_paise == 0:
        return replace(due, status="PAID")
    if due.due_date > as_of:
        return replace(due, status="UPCOMING")
    if as_of > due.due_date + timedelta(days=grace):
        return replace(due, status="OVERDUE")
    return replace(due, status="PARTIAL")


def _run_fold(loan: Loan, events: list, as_of: date) -> EngineResult:
    """The single date-ordered fold. Returns a full EngineResult."""
    monthly = loan.monthly_rate
    rate_unit = RateUnit.PER_MONTH
    principal = 0
    credit = 0

    # timeline pieces: [start, principal, monthly_rate, rate_unit]
    pieces: list[tuple[date, int, Decimal, RateUnit]] = []
    open_start = loan.start_date
    dues: list[Due] = []
    next_boundary = next_due_day(loan.due_day, loan.start_date)
    allocations: list[Allocation] = []

    def close_due(up_to: date) -> None:
        """Materialise every period boundary in (open_start, up_to]."""
        nonlocal open_start, next_boundary, principal, monthly, rate_unit
        b = next_boundary
        while b <= up_to:
            due = _new_due(len(dues) + 1, loan, open_start, b)
            exact = _period_interest(pieces, open_start, b)
            due = replace(due, interest_paise=half_up_paise(exact))
            # COMPOUND: unpaid interest re-enters principal at the close
            if loan.interest_type is InterestType.COMPOUND:
                unpaid = due.interest_paise - due.paid_paise - due.waived_paise
                if unpaid > 0:
                    principal += unpaid
                    due = replace(due, capitalised_paise=unpaid, status="PAID")
                    pieces.append((b, principal, monthly, rate_unit))
            dues.append(due)
            open_start = b
            b = next_due_day(loan.due_day, open_start)
            next_boundary = b
            if as_of < open_start:
                break

    for e in events:
        if e.entry_date > as_of:
            continue
        # any due boundary up to and including this event's day closes first
        close_due(e.entry_date)

        if e.type is EntryType.RATE_CHANGE:
            if e.new_rate is None:
                raise ValueError(f"RATE_CHANGE entry {e.id} carries no new rate")
            monthly = e.new_rate if rate_unit is RateUnit.PER_MONTH else e.new_rate
            # RATE_CHANGE supplies the rate in the loan's own unit
            if loan.rate_unit is RateUnit.PER_MONTH:
                monthly, rate_unit = e.new_rate, RateUnit.PER_MONTH
            else:
                monthly, rate_unit = e.new_rate, RateUnit.PER_YEAR
            pieces.append((e.entry_date, principal, monthly, rate_unit))

        elif e.type is EntryType.WAIVER:
            target = (e.reference or "INTEREST").upper()
            amt = e.amount_paise
            if target == "PENALTY":
                for d in dues:
                    if amt <= 0:
                        break
                    open_pen = d.penalty_paise - d.penalty_paid_paise
                    take = min(amt, open_pen)
                    d = replace(d, penalty_paid_paise=d.penalty_paid_paise + take)
                    dues[dues.index(d)] = d
                    amt -= take
            else:
                for i, d in enumerate(dues):
                    if amt <= 0:
                        break
                    open_int = d.interest_paise - d.paid_paise - d.waived_paise
                    take = min(amt, open_int)
                    dues[i] = replace(d, waived_paise=d.waived_paise + take)
                    amt -= take

        elif e.type is EntryType.PENALTY:
            # posted by the daily job; attaches to a due (reference = due index)
            if e.reference:
                try:
                    i = int(e.reference)
                    if 1 <= i <= len(dues):
                        dues[i - 1] = replace(dues[i - 1],
                                              penalty_paise=dues[i - 1].penalty_paise + e.amount_paise)
                except ValueError:
                    pass  # free-standing penalty, tracked at loan level
            # else: loan-level penalty (kept out of any due's bucket)

        elif e.type in (EntryType.PAYMENT, EntryType.PRINCIPAL_PREPAY):
            avail = e.amount_paise + credit
            credit = 0
            # 1) penalty, oldest due first
            for i, d in enumerate(dues):
                if avail <= 0:
                    break
                open_pen = d.penalty_paise - d.penalty_paid_paise
                take = min(avail, open_pen)
                if take > 0:
                    dues[i] = replace(d, penalty_paid_paise=d.penalty_paid_paise + take)
                    allocations.append(Allocation(e.id, "PENALTY", d.index, take))
                    avail -= take
            # 2) interest, oldest due first
            for i, d in enumerate(dues):
                if avail <= 0:
                    break
                open_int = d.interest_paise - d.paid_paise - d.waived_paise
                take = min(avail, open_int)
                if take > 0:
                    dues[i] = replace(d, paid_paise=d.paid_paise + take)
                    allocations.append(Allocation(e.id, "INTEREST", d.index, take))
                    avail -= take
            # 3) principal (clamped; remainder is credit)
            take = min(avail, principal)
            if take > 0:
                allocations.append(Allocation(e.id, "PRINCIPAL", None, take))
                principal -= take
                avail -= take
                pieces.append((e.entry_date, principal, monthly, rate_unit))
            credit = avail

        elif e.type is EntryType.REVERSAL:
            # logical exclusion was done before the fold; nothing to apply
            pass

        else:  # DISBURSE / TOPUP
            principal += e.amount_paise
            pieces.append((e.entry_date, principal, monthly, rate_unit))

    # no event between the last one and as_of can open new boundaries,
    # but closes are due on their boundary day even without events.
    close_due(as_of)

    # in-flight period [open_start, as_of)
    open_exact = _period_interest(pieces, open_start, as_of)
    open_paise = half_up_paise(open_exact)
    interest_closed = sum(d.interest_paise for d in dues)
    interest_received = sum(d.paid_paise for d in dues)
    interest_waived = sum(d.waived_paise for d in dues)
    pen_posted = sum(d.penalty_paise for d in dues)
    pen_paid = sum(d.penalty_paid_paise for d in dues)
    interest_outstanding = max(0, interest_closed - interest_received - interest_waived
                               - sum(d.capitalised_paise for d in dues))
    penalty_outstanding = pen_posted - pen_paid
    payoff = principal + interest_outstanding + penalty_outstanding
    payoff_breakdown = PayoffBreakdown(
        principal_paise=principal,
        interest_closed_paise=interest_outstanding,
        interest_open_paise=open_paise,
        penalty_paise=penalty_outstanding,
        credit_paise=credit,
        total_paise=payoff,
    )
    balances = Balances(
        principal_outstanding_paise=principal,
        interest_accrued_paise=interest_closed,
        interest_open_paise=open_paise,
        interest_received_paise=interest_received,
        interest_waived_paise=interest_waived,
        penalty_posted_paise=pen_posted,
        penalty_paid_paise=pen_paid,
        credit_paise=credit,
        payoff_paise=payoff,
        payoff=payoff_breakdown,
    )

    # recompute statuses as of `as_of`
    due_rows = tuple(_status_for(d, as_of, loan.grace_days) for d in dues)
    timeline = tuple(
        TimelinePiece(start=p[0], principal_paise=p[1], monthly_rate=p[2],
                      rate_unit=p[3],
                      end=pieces[i + 1][0] if i + 1 < len(pieces) else None)
        for i, p in enumerate(pieces)
    )
    predicted = predict_dues(loan, events, as_of)
    return EngineResult(
        loan_id=loan.id, as_of=as_of, balances=balances, dues=due_rows,
        allocations=tuple(allocations), timeline=timeline,
        last_closed_due_date=open_start,
        predicted=predicted,
    )


def _predict_single(loan: Loan, events: list, as_of: date,
                    period_start: date, period_end: date) -> int:
    """Constant-principal estimate for a future period (no events beyond
    `as_of` are known). Used for the cash calendar and reminders."""
    r = _run_fold(loan, events, period_start)
    principal = r.balances.principal_outstanding_paise
    # rate in force at period start
    rate_p = r.timeline[-1] if r.timeline else None
    monthly = rate_p.monthly_rate if rate_p else loan.monthly_rate
    unit = rate_p.rate_unit if rate_p else RateUnit.PER_MONTH
    exact = _period_interest([(period_start, principal, monthly, unit)],
                             period_start, period_end)
    return half_up_paise(exact)


def predict_dues(loan: Loan, events: list, as_of: date,
                 days_ahead: int = 365) -> tuple[PredictedDue, ...]:
    r = _run_fold(loan, events, as_of)
    start = r.last_closed_due_date or loan.start_date
    n = len(r.dues)
    out: list[PredictedDue] = []
    b = next_due_day(loan.due_day, start)
    while b <= as_of + timedelta(days=days_ahead):
        out.append(PredictedDue(
            index=n + 1 + len(out), due_date=b, period_start=start,
            estimated_paise=_predict_single(loan, events, as_of, start, b),
        ))
        start = b
        b = next_due_day(loan.due_day, start)
    return tuple(out)


def compute(loan: Loan, entries: list, as_of: date) -> EngineResult:
    """The public pure function: terms + entries + date -> results."""
    return _run_fold(loan, _effective_events(loan, entries), as_of)


# --------------------------------------------------------------------------
# Funding-link analytics (spec 6.7 / 6.8)
# --------------------------------------------------------------------------


def funding_spread(taken: Loan, links: list[FundingLink]) -> FundingSpread:
    """`link.amount × (r_given − r_taken)`, monthly-normalised.

    Negative spread means we lent cheaper than we borrowed and is
    flagged. `idle_paise` = taken principal not covered by any link:
    borrowed money earning nothing.
    """
    if not links:
        return FundingSpread(taken.id, 0, taken.principal_paise, 0,
                             taken.principal_paise)
    amount = sum(l.amount_paise for l in links)
    idle = max(0, taken.principal_paise - amount)
    r_t = taken.monthly_rate if taken.rate_unit is RateUnit.PER_MONTH else taken.rate / Decimal(12)
    monthly = 0
    for l in links:
        given = links  # caller resolves given loans; we need their rates
        break
    # NOTE: spread needs the GIVEN loan's rate; the caller (API layer)
    # passes both sides. For the pure engine we expose a helper:
    return FundingSpread(
        taken_loan_id=taken.id,
        given_loan_id=links[0].given_loan_id if links else 0,
        amount_paise=amount,
        monthly_spread_paise=0,  # placeholder; use funding_spread_pair()
        idle_paise=idle,
    )


def funding_spread_pair(taken: Loan, given: Loan, link: FundingLink) -> FundingSpread:
    """Exact pair-wise spread (spec 6.7): `amount × (r_given − r_taken)`."""
    r_t = taken.rate if taken.rate_unit is RateUnit.PER_YEAR else taken.monthly_rate
    r_g = given.rate if given.rate_unit is RateUnit.PER_YEAR else given.monthly_rate
    monthly = half_up_paise(Decimal(link.amount_paise) * (r_g - r_t) * 100)
    # NOTE: ×100 converts paise→rupees so the spread reads in ₹/month
    return FundingSpread(
        taken_loan_id=taken.id,
        given_loan_id=given.id,
        amount_paise=link.amount_paise,
        monthly_spread_paise=monthly,
        idle_paise=max(0, taken.principal_paise - link.amount_paise),
    )


def cash_flow_mismatches(taken: Loan, given: Loan, link: FundingLink,
                         given_entries: list, as_of: date) -> list[MismatchWarning]:
    """Spec 6.8. For a TAKEN loan T with next due D and a linked GIVEN loan G:

    * G's next due is after D -> "payable before receivable" WARNING
    * G has overdue dues -> RISK: T's interest must come from own funds
    * G is WRITTEN_OFF -> remaining exposure on T is at risk
    """
    out: list[MismatchWarning] = []
    d = next_due_day(taken.due_day, as_of - timedelta(days=1))
    if d <= as_of:
        d = next_due_day(taken.due_day, as_of)
    g_dues = _run_fold(given, _effective_events(given, given_entries), as_of)
    g_due = next_due_day(given.due_day, as_of - timedelta(days=1))
    if g_due <= as_of:
        g_due = next_due_day(given.due_day, as_of)
    if g_due > d:
        out.append(MismatchWarning(
            taken_loan_id=taken.id, given_loan_id=given.id,
            kind="PAY_BEFORE_RECEIVE",
            detail=(f"GIVEN #{given.id} next due {g_due.isoformat()} is after "
                    f"TAKEN #{taken.id} due {d.isoformat()}; the payable "
                    f"must come from own funds for { (g_due - d).days } days."),
        ))
    if any(d.late for d in g_dues.dues):
        out.append(MismatchWarning(
            taken_loan_id=taken.id, given_loan_id=given.id,
            kind="GIVEN_OVERDUE",
            detail=(f"GIVEN #{given.id} has overdue dues; interest on "
                    f"TAKEN #{taken.id} must come from own funds."),
        ))
    if given.status == "WRITTEN_OFF":
        out.append(MismatchWarning(
            taken_loan_id=taken.id, given_loan_id=given.id,
            kind="GIVEN_WRITTEN_OFF",
            detail=(f"GIVEN #{given.id} is written off; remaining exposure "
                    f"on TAKEN #{taken.id} of "
                    f"{g_dues.balances.payoff_paise} paise is at risk."),
        ))
    return out
