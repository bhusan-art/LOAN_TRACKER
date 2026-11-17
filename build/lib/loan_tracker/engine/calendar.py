"""Calendar helpers for cycle boundaries (engine.md §4.2, DECISIONS #14).

Pure date math on datetime.date — no I/O, no clock.
"""
import calendar as _calendar
from datetime import date
from typing import Iterator

from loan_tracker.engine.types import LoanTerms


def days_in_month(year: int, month: int) -> int:
    """Actual calendar days in (year, month); Feb = 28 or 29 (leap-aware)."""
    return _calendar.monthrange(year, month)[1]


def clamp_due_day(year: int, month: int, due_day: int) -> int:
    """Clamp a 1..31 due day to the last day of (year, month) if it is shorter.

    31 -> 30 / 29 / 28 (review 2.5); a due_day that fits is returned as-is.
    """
    return min(due_day, days_in_month(year, month))


def _add_months(d: date, months: int) -> date:
    """Shift d by whole calendar months, clamping the day to month-end."""
    idx = d.year * 12 + (d.month - 1) + months
    year, month = divmod(idx, 12)
    return date(year, month + 1, min(d.day, days_in_month(year, month + 1)))


def _first_due_day_on_or_after(base: date, due_day: int) -> date:
    """First clamped due_day occurrence on or after base (never skips a month)."""
    idx = base.year * 12 + base.month - 1
    while True:
        year, month = divmod(idx, 12)
        cand = date(year, month + 1, clamp_due_day(year, month + 1, due_day))
        if cand >= base:
            return cand
        idx += 1


def _first_due_day_strictly_after(start: date, due_day: int) -> date:
    """First clamped due_day occurrence STRICTLY after start (§4.2, pinned by
    Example A: start 1 Jan, due_day 1 -> 1 Feb)."""
    cand = _first_due_day_on_or_after(start, due_day)
    if cand == start:
        year, month = divmod(start.year * 12 + start.month, 12)  # next month
        cand = _first_due_day_on_or_after(date(year, month + 1, 1), due_day)
    return cand


def _advance(b: date, due_day: int, step_months: int) -> date:
    """First clamped due_day occurrence step_months months after b's month."""
    idx = (b.year * 12 + b.month - 1) + step_months
    year, month = divmod(idx, 12)
    return _first_due_day_on_or_after(date(year, month + 1, 1), due_day)


def _is_maturity_lump(terms: LoanTerms, b: date) -> bool:
    return (
        terms.interest_cycle == "AT_MATURITY"
        and terms.maturity_date is not None
        and b == terms.maturity_date
    )


def iter_boundaries(terms: LoanTerms) -> Iterator[date]:
    """Endless due-date boundaries b_1, b_2, ... (engine.md §4.2).

    - MONTHLY: b_1 = first clamped due_day STRICTLY AFTER start_date, then
      each following month (due_day clamped to month-end every month).
    - QUARTERLY (#14): b_1 = first clamped due_day occurrence on/after
      start_date + 3 months, then every 3 months.
    - AT_MATURITY (#12): a single lump boundary at maturity_date, then the
      first clamped due_day strictly after maturity and monthly after that.
      maturity_date = None falls back to plain monthly.
    """
    due_day = terms.due_day
    if terms.interest_cycle == "AT_MATURITY" and terms.maturity_date is not None:
        yield terms.maturity_date
        b, step = _first_due_day_strictly_after(terms.maturity_date, due_day), 1
    elif terms.interest_cycle == "QUARTERLY":
        b, step = _first_due_day_on_or_after(_add_months(terms.start_date, 3), due_day), 3
    else:
        b, step = _first_due_day_strictly_after(terms.start_date, due_day), 1
    while True:
        yield b
        b = _advance(b, due_day, step)


def cycle_boundaries(terms: LoanTerms, up_to: date) -> list[date]:
    """All boundaries b_k <= up_to. Period k accrues on [b_{k-1}, b_k - 1]
    inclusive (b_0 = start_date); due_date b_k is the first day of period k+1."""
    out: list[date] = []
    for b in iter_boundaries(terms):
        if b > up_to:
            return out
        out.append(b)
    return out  # pragma: no cover (generator is endless)


def cycle_frame(terms: LoanTerms, b: date) -> tuple[date, int]:
    """The regular cycle that ends at boundary b: (cycle_start, flat_months).

    DECISIONS #15: a complete cycle earns exactly P x r x flat_months; any
    period shorter or longer than [cycle_start, b - 1] is pro-rated by
    days / (b - cycle_start). For a regular period cycle_start equals the
    previous boundary; for a short first period (start 20 Jan, due_day 5)
    it is the notional previous due day (5 Jan), so 16 of 31 days are charged.
    """
    if _is_maturity_lump(terms, b):
        s = terms.start_date
        months = max(1, (b.year * 12 + b.month) - (s.year * 12 + s.month))
        return _add_months(b, -months), months
    months = 3 if terms.interest_cycle == "QUARTERLY" else 1
    return _advance(b, terms.due_day, -months), months
