"""Accrual and due generation (docs/design/engine.md §3.1, §4; DECISIONS #15).

Pure: terms + live entries + as_of + principal timeline in, exact results
out. No I/O, no clock. All arithmetic is exact Decimal in paise; rounding
happens only at the two defined points (§4.1) — due generation and the open
period.

DECISIONS #15: a full cycle earns exactly P*r (monthly) or 3*P*r (quarterly),
regardless of how many days it has. Only cycles split by a principal or rate
event pro-rate: piece = P_i*r_i/100 * m * days_piece / days_in_cycle, where
m is the cycle's flat months and days_in_cycle = b_k - b_{k-1}. PER_YEAR
loans keep the daily rate r/365 (04.1).

Principal timeline: date-ordered [(d, P)] where P paise are in force FROM
day d onward; P may be 0 (a cell from a payoff day accrues nothing). Phase
2.2 feeds it explicitly; Phase 2.3's waterfall derives it from live entries,
at which point the pinned §2 signature re-converges.
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from loan_tracker.engine.calendar import (
    _add_months,
    _advance,
    _first_due_day_on_or_after,
    _first_due_day_strictly_after,
    cycle_boundaries,
)
from loan_tracker.engine.money import round_half_up
from loan_tracker.engine.types import (
    AccrualResult,
    Due,
    LedgerEntry,
    LoanTerms,
    ProjectedDue,
    Segment,
)

_ONE_DAY = timedelta(days=1)

# P in force from day d onward, date-ordered; P may be 0.
PrincipalTimeline = list[tuple[date, int]]


def _live(entries: Sequence[LedgerEntry]) -> list[LedgerEntry]:
    """Drop reversed originals and their REVERSALs (review 2.2 netting)."""
    reversing = {e.reverses for e in entries if e.type == "REVERSAL" and e.reverses is not None}
    return [e for e in entries if e.entry_id not in reversing and e.type != "REVERSAL"]


def net_rate(terms: LoanTerms, entries: Sequence[LedgerEntry]) -> list[tuple[date, Decimal]]:
    """Valid RATE_CHANGE history: [(effective_date, rate)] in date order.

    The initial rate (terms.rate, from start_date) comes from the terms, not
    the entries, so this helper takes both — the pinned §2 signature
    net_rate(entries) cannot see the initial rate. v1 RATE_CHANGE changes
    the value only, never rate_unit (§1 note).
    """
    steps: list[tuple[date, Decimal]] = [(terms.start_date, terms.rate)]
    by_date: dict[date, Decimal] = {}
    for e in sorted(_live(entries), key=lambda e: (e.entry_date, e.entry_id)):
        if e.type == "RATE_CHANGE" and e.new_rate is not None:
            by_date[e.entry_date] = e.new_rate
    steps.extend(sorted(by_date.items()))
    return steps


def _principal_at(principal_tl: PrincipalTimeline, d: date) -> int:
    """P in force on day d (last (step_date, P) with step_date <= d)."""
    p = 0
    for step_date, step_p in principal_tl:
        if step_date <= d:
            p = step_p
        else:
            break
    return p


def _rate_at(rate_steps: list[tuple[date, Decimal]], d: date) -> Decimal:
    r = rate_steps[0][1]
    for step_date, step_r in rate_steps:
        if step_date <= d:
            r = step_r
        else:
            break
    return r


def cycle_months(terms: LoanTerms, prev: date, b: date) -> int:
    """Flat months of period [prev, b - 1] (DECISIONS #15): MONTHLY = 1,
    QUARTERLY = 3, the AT_MATURITY lump (start_date -> maturity_date) = the
    months between the two dates (>= 1); post-maturity months = 1."""
    if terms.interest_cycle == "QUARTERLY":
        return 3
    if (
        terms.interest_cycle == "AT_MATURITY"
        and prev == terms.start_date
        and b == terms.maturity_date
    ):
        delta = (b.year * 12 + b.month) - (terms.start_date.year * 12 + terms.start_date.month)
        return max(1, delta)
    return 1


def _next_boundary_after(terms: LoanTerms, as_of: date) -> date:
    """First cycle boundary STRICTLY after as_of (any cycle type; #12: the
    maturity itself when still pending, else monthly)."""
    if (
        terms.interest_cycle == "AT_MATURITY"
        and terms.maturity_date is not None
        and terms.maturity_date > as_of
    ):
        return terms.maturity_date
    if terms.interest_cycle == "QUARTERLY":
        realized = cycle_boundaries(terms, as_of)
        base = _first_due_day_on_or_after(_add_months(terms.start_date, 3), terms.due_day)
        return _advance(realized[-1], terms.due_day, 3) if realized else base
    return _first_due_day_strictly_after(as_of, terms.due_day)


def _segments(
    terms: LoanTerms,
    rate_steps: list[tuple[date, Decimal]],
    principal_tl: PrincipalTimeline,
    from_day: date,
    to_day: date,
    cycle_days: int,
    months: int,
) -> list[Segment]:
    """Tile [from_day, to_day] into segments split ONLY at rate/principal
    step dates (DECISIONS #15: no calendar-month-edge splits). A step dated
    day s takes effect FROM s (the repayment day belongs to the new segment,
    04.1). Exact Decimal; rounded only at a generation point (§4.1). P = 0
    cells accrue nothing.

    PER_MONTH piece = P * r/100 * months * days_piece / cycle_days (#15) —
    a no-event cycle gives exactly P * r * months. PER_YEAR piece =
    P * r/100 * days / 365 (04.1).
    """
    out: list[Segment] = []
    if to_day < from_day:
        return out
    splits = sorted(
        {d for d, _ in rate_steps if from_day < d <= to_day}
        | {d for d, _ in principal_tl if from_day < d <= to_day}
    )
    cur = from_day
    for s in [*splits, to_day + _ONE_DAY]:
        seg_end = s - _ONE_DAY
        if seg_end >= cur:
            p = _principal_at(principal_tl, cur)
            r = _rate_at(rate_steps, cur)
            days = (seg_end - cur).days + 1
            if p > 0:
                if terms.rate_unit == "PER_MONTH":
                    exact = (
                        Decimal(p) * r / Decimal(100) * Decimal(months) * Decimal(days)
                        / Decimal(cycle_days)
                    )
                else:  # PER_YEAR
                    exact = Decimal(p) * r / Decimal(100) * Decimal(days) / Decimal(365)
                out.append(Segment(cur, seg_end, p, r, days, exact))
        cur = s
    return out


def _make_due(terms: LoanTerms, period_start: date, due_date: date, segs: list[Segment]) -> Due:
    exact = sum((s.interest_exact for s in segs), Decimal(0))
    return Due(
        loan_id=terms.loan_id,
        period_start=period_start,
        period_end=due_date - _ONE_DAY,
        due_date=due_date,
        interest_paise=round_half_up(exact),  # §4.1-a generation point
    )


def _zero_date(principal_tl: PrincipalTimeline) -> date | None:
    """First date on which principal reaches 0 (None = never in the timeline)."""
    for d, p in principal_tl:
        if p == 0:
            return d
    return None


def projected_due(
    terms: LoanTerms,
    entries: Sequence[LedgerEntry],
    as_of: date,
    principal_tl: PrincipalTimeline,
) -> ProjectedDue | None:
    """The next cycle held flat at current P & rate (review 2.3):
    informational, not part of accrued/payoff. None only when P(as_of) = 0
    (#12: NOT at maturity)."""
    if _principal_at(principal_tl, as_of) == 0:
        return None
    p = _principal_at(principal_tl, as_of)
    r = _rate_at(net_rate(terms, entries), as_of)
    realized = cycle_boundaries(terms, as_of)
    prev = realized[-1] if realized else terms.start_date
    next_b = _next_boundary_after(terms, as_of)
    months = cycle_months(terms, prev, next_b)
    if terms.rate_unit == "PER_MONTH":
        exact = Decimal(p) * r / Decimal(100) * Decimal(months)  # full cycle (#15)
    else:
        exact = Decimal(p) * r / Decimal(100) * Decimal((next_b - prev).days) / Decimal(365)
    return ProjectedDue(
        loan_id=terms.loan_id,
        period_start=prev,
        period_end=next_b - _ONE_DAY,
        due_date=next_b,
        interest_paise=round_half_up(exact),
    )


def generate_dues(
    terms: LoanTerms,
    entries: Sequence[LedgerEntry],
    as_of: date,
    principal_tl: PrincipalTimeline,
) -> tuple[list[Due], ProjectedDue | None]:
    """Full realized set (due_date <= as_of) keyed by (loan_id, period_start)
    + the projected next due (review 2.3). No `realized` input: idempotency
    is the unique index (§2). COMPOUND/EMI raise (#1, §6).

    Payoff rule (§3.1/§3.2, #11): when the timeline reaches 0 mid-cycle on
    day Z <= as_of, the open tail [last boundary, Z - 1] is realized as a
    CLOSING DUE with due_date = Z, pro-rated by its cycle's days (#15); no
    dues are generated after principal reaches 0. A zero on a boundary is
    just that boundary's normal due."""
    if terms.interest_type != "SIMPLE":
        raise NotImplementedError(f"v1 supports SIMPLE only (DECISIONS #1), got {terms.interest_type}")
    rate_steps = net_rate(terms, entries)
    zero = _zero_date(principal_tl)
    stop = min(as_of, zero) if zero is not None else as_of
    prev = terms.start_date
    bounds: list[date] = []
    dues: list[Due] = []
    for b in cycle_boundaries(terms, stop):
        bounds.append(b)
        cycle_days = (b - prev).days
        months = cycle_months(terms, prev, b)
        segs = _segments(terms, rate_steps, principal_tl, prev, b - _ONE_DAY, cycle_days, months)
        dues.append(_make_due(terms, prev, b, segs))
        prev = b
    if zero is not None and zero <= as_of and zero not in bounds and zero - _ONE_DAY >= prev:
        next_b = _next_boundary_after(terms, zero - _ONE_DAY)
        cycle_days = (next_b - prev).days
        months = cycle_months(terms, prev, next_b)
        segs = _segments(terms, rate_steps, principal_tl, prev, zero - _ONE_DAY, cycle_days, months)
        dues.append(_make_due(terms, prev, zero, segs))
    return dues, projected_due(terms, entries, as_of, principal_tl)


def open_period(
    terms: LoanTerms,
    entries: Sequence[LedgerEntry],
    as_of: date,
    principal_tl: PrincipalTimeline,
) -> int:
    """In-progress tail [last realized boundary, as_of - 1] (#11), pro-rated
    by the containing cycle's days (#15), rounded once (§4.1-b): 0 when as_of
    is on a boundary or principal is fully repaid; before the first boundary
    the tail runs from start_date."""
    if terms.interest_type != "SIMPLE":
        raise NotImplementedError(f"v1 supports SIMPLE only (DECISIONS #1), got {terms.interest_type}")
    if _principal_at(principal_tl, as_of) == 0:
        return 0
    rate_steps = net_rate(terms, entries)
    realized = cycle_boundaries(terms, as_of)
    prev = realized[-1] if realized else terms.start_date
    last = as_of - _ONE_DAY
    if last < prev:
        return 0
    next_b = _next_boundary_after(terms, last)
    segs = _segments(
        terms, rate_steps, principal_tl, prev, last,
        (next_b - prev).days, cycle_months(terms, prev, next_b),
    )
    return round_half_up(sum((s.interest_exact for s in segs), Decimal(0)))


def accrue(
    terms: LoanTerms,
    entries: Sequence[LedgerEntry],
    as_of: date,
    principal_tl: PrincipalTimeline,
) -> AccrualResult:
    """Per-loan accrual up to and including as_of (§3.1): realized dues
    rounded once at generation, exact segment table, REALIZED accrued basis
    (due_date <= as_of), open tail (#11), projected next due."""
    if terms.interest_type != "SIMPLE":
        raise NotImplementedError(f"v1 supports SIMPLE only (DECISIONS #1), got {terms.interest_type}")
    dues, proj = generate_dues(terms, entries, as_of, principal_tl)
    rate_steps = net_rate(terms, entries)
    segments: list[Segment] = []
    prev = terms.start_date
    for d in dues:
        b = d.due_date
        segments.extend(
            _segments(
                terms, rate_steps, principal_tl, prev, d.period_end,
                (b - prev).days, cycle_months(terms, prev, b),
            )
        )
        prev = b
    last = as_of - _ONE_DAY
    if last >= prev:
        next_b = _next_boundary_after(terms, last)
        segments.extend(
            _segments(
                terms, rate_steps, principal_tl, prev, last,
                (next_b - prev).days, cycle_months(terms, prev, next_b),
            )
        )
    return AccrualResult(
        periods=tuple(dues),
        projected_due=proj,
        interest_accrued_paise=sum(d.interest_paise for d in dues),
        open_period_paise=open_period(terms, entries, as_of, principal_tl),
        segments=tuple(segments),
    )
