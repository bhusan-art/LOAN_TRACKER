"""Calendar helpers for cycle boundaries (engine.md §4.2, DECISIONS #14).

Pure date math on datetime.date — no I/O, no clock.
"""
import calendar as _calendar
from datetime import date

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


def cycle_boundaries(terms: LoanTerms, up_to: date) -> list[date]:
    """Due-date boundaries b_1, b_2, ... with b_k <= up_to (engine.md §4.2).

    - MONTHLY: b_1 = first clamped due_day STRICTLY AFTER start_date, then
      each following month (due_day clamped to month-end every month).
    - QUARTERLY (#14): b_1 = first clamped due_day occurrence on/after
      start_date + 3 months, then every 3 months.
    - AT_MATURITY (#12): a single lump boundary at maturity_date, then
      monthly clamped due_day occurrences on the unpaid balance. An
      AT_MATURITY loan with maturity_date = None falls back to plain monthly.

    Period k accrues on [b_{k-1}, b_k - 1] inclusive (b_0 = start_date - 1);
    due_date b_k is the first day of period k+1.
    """
    due_day = terms.due_day
    if terms.interest_cycle == "AT_MATURITY" and terms.maturity_date is not None:
        first, step = terms.maturity_date, 1
    elif terms.interest_cycle == "QUARTERLY":
        base = _add_months(terms.start_date, 3)  # start_date + 3 months (#14)
        first, step = _first_due_day_on_or_after(base, due_day), 3
    else:  # MONTHLY, and AT_MATURITY without a maturity date (open-ended)
        first, step = _first_due_day_strictly_after(terms.start_date, due_day), 1

    out: list[date] = []
    b = first
    while b <= up_to:
        out.append(b)
        b = _advance(b, due_day, step)
    return out
