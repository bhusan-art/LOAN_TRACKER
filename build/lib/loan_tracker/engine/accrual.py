"""Interest arithmetic for one period (docs/design/engine.md §3.1, §4;
DECISIONS #10, #15).

Pure helpers used by the replay (replay.py). All arithmetic is exact
Decimal in paise; rounding happens only where a due or the open period is
generated (§4.1), via money.round_half_up.

DECISIONS #15 (PER_MONTH loans): a complete cycle earns exactly
P x r x flat_months. A period that is not a complete cycle (short first
period, mid-cycle payoff, principal or rate change inside a cycle) is
pro-rated: piece = P_i x r_i/100 x months x days_i / cycle_days, where the
cycle is the regular cycle ending at the period's boundary (calendar.cycle_frame).
PER_YEAR loans use the daily rate r/365 (04.1).
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from loan_tracker.engine.calendar import cycle_frame
from loan_tracker.engine.types import LedgerEntry, LoanTerms, Segment

ONE_DAY = timedelta(days=1)

# P in force FROM day d onward, date-ordered; P may be 0.
PrincipalTimeline = list[tuple[date, int]]
RateSteps = list[tuple[date, Decimal]]


def live_entries(entries: Sequence[LedgerEntry]) -> list[LedgerEntry]:
    """Drop reversed originals and the REVERSAL rows themselves (review 2.2),
    then order by entry_date, money-in first (DISBURSE/TOPUP before anything
    else that day), then entry_id."""
    reversed_ids = {e.reverses for e in entries if e.type == "REVERSAL" and e.reverses is not None}
    live = [e for e in entries if e.type != "REVERSAL" and e.entry_id not in reversed_ids]
    return sorted(live, key=lambda e: (e.entry_date, e.type not in ("DISBURSE", "TOPUP"), e.entry_id))


def net_rate(terms: LoanTerms, entries: Sequence[LedgerEntry]) -> RateSteps:
    """[(effective_from, rate)], date order: the initial rate from start_date,
    then each live RATE_CHANGE (the last one on a given day wins)."""
    by_date: dict[date, Decimal] = {}
    for e in live_entries(entries):
        if e.type == "RATE_CHANGE" and e.new_rate is not None:
            by_date[e.entry_date] = e.new_rate
    return [(terms.start_date, terms.rate), *sorted(by_date.items())]


def value_at(steps: Sequence[tuple[date, object]], d: date, default):
    """Value of the last step dated <= d (default before the first step)."""
    v = default
    for step_date, step_v in steps:
        if step_date <= d:
            v = step_v
        else:
            break
    return v


def period_segments(
    terms: LoanTerms,
    rates: RateSteps,
    principal_tl: PrincipalTimeline,
    from_day: date,
    to_day: date,
    boundary: date,
) -> list[Segment]:
    """Exact interest pieces for the days [from_day, to_day] (inclusive) of
    the period that falls due on `boundary`. Pieces split only where the
    rate or the principal changes (an event dated d applies from d, 04.1).
    Zero-principal days produce no segment."""
    if to_day < from_day:
        return []
    cycle_start, months = cycle_frame(terms, boundary)
    cycle_days = Decimal((boundary - cycle_start).days)
    splits = sorted(
        {d for d, _ in rates if from_day < d <= to_day}
        | {d for d, _ in principal_tl if from_day < d <= to_day}
    )
    out: list[Segment] = []
    cur = from_day
    for nxt in [*splits, to_day + ONE_DAY]:
        end = nxt - ONE_DAY
        if end >= cur:
            p = value_at(principal_tl, cur, 0)
            r = value_at(rates, cur, terms.rate)
            days = (end - cur).days + 1
            if p > 0:
                if terms.rate_unit == "PER_MONTH":
                    exact = Decimal(p) * r / 100 * months * days / cycle_days
                else:  # PER_YEAR
                    exact = Decimal(p) * r / 100 * days / 365
                out.append(Segment(cur, end, p, r, days, exact))
        cur = nxt
    return out


def segments_total(segs: Sequence[Segment]) -> Decimal:
    return sum((s.interest_exact for s in segs), Decimal(0))
