"""Interest accrual: day-count rules and period math (spec 6.1).

Two documented conventions:

* PER_MONTH ("₹X per ₹100 per month", monthly flat — the main case):
  a full calendar cycle earns P × monthly_rate. A partial span is
  pro-rated: monthly_rate × days ÷ days_in_that_calendar_month. A full
  cycle inside one month is therefore exactly P × monthly_rate.

* PER_YEAR: daily rate = annual_rate ÷ 365 (Actual/365). No
  calendar-month pro-rating.

In both cases interest is charged from the disbursement day and the
repayment/due day is *excluded*: an accrual interval [a, b) covers the
days a, a+1, …, b-1. All accrual here is exact (Decimal paise);
rounding to integer paise happens only at defined points (due close,
payoff), per P3.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal

from .models import InterestType, Loan, RateUnit

__all__ = ["accrue_interval", "due_dates_between", "next_due_day", "days_in_month"]


def days_in_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def accrue_interval(
    principal_paise: int,
    loan: Loan,
    start: date,
    end: date,
) -> Decimal:
    """Exact (possibly fractional) paise of interest for [start, end).

    The interval must lie within a single calendar month and have no
    due-day or event boundary inside it — the caller (the engine's
    timeline walk) guarantees this.
    """
    if start >= end:
        return Decimal(0)
    days = (end - start).days
    p = Decimal(principal_paise)
    if loan.rate_unit is RateUnit.PER_YEAR:
        # Actual/365: daily rate = annual ÷ 365.
        return p * loan.rate / Decimal(365) * days
    # PER_MONTH: calendar-month pro-rating. Interval is inside one month,
    # so P × monthly_rate × days/month_days is exact when days == month_days.
    return p * loan.monthly_rate * days / Decimal(days_in_month(start))


def next_due_day(due_day: int, strictly_after: date) -> date:
    """Next occurrence of `due_day` clamped to month length, strictly after
    the given date.

    Due-day clamping (edge case "31st due day in a 30-day month"): a 31st
    due day falls on the last day of shorter months (30th, 28th/29th).
    """
    after = strictly_after
    y, m = after.year, after.month
    for _ in range(48):  # search up to 4 years forward (covers 31->31 gaps)
        candidate = date(y, m, min(due_day, days_in_month(date(y, m, 1))))
        if candidate > after:
            return candidate
        m += 1
        if m > 12:
            m, y = 1, y + 1
    raise ValueError("due day not found within 48 months")


def due_dates_between(loan: Loan, start: date, end: date) -> list[date]:
    """Due dates in (start, end]: period k runs [boundary_{k-1}, due_k).

    The first period starts at the loan's start date; its due date is the
    first due day strictly after the start date (a loan started on its
    due day gets a full first cycle, matching worked example A).
    """
    out: list[date] = []
    d = next_due_day(loan.due_day, on_or_after=start, strictly_after=start)
    while d <= end:
        out.append(d)
        d = next_due_day(loan.due_day, on_or_after=d, strictly_after=d)
    return out
