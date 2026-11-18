"""Cash forecast arithmetic (Phase 11). Pure: no database, no clock.

reports.cash_forecast() collects the flows (interest due in and out from the
engine, principal at maturity, planned items) and the opening balance (latest
balance of each cash/bank account); this module only adds them up.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable


@dataclass(frozen=True)
class Flow:
    day: date
    amount_paise: int        # + money in, - money out
    risky: bool = False      # money in from a borrower who is already late (left out when cautious)


@dataclass
class ForecastDay:
    day: date
    in_paise: int
    out_paise: int
    running_paise: int


@dataclass
class Forecast:
    opening_paise: int
    days: list[ForecastDay] = field(default_factory=list)
    low_paise: int = 0
    low_date: date | None = None
    short_date: date | None = None   # first day the balance falls below the buffer
    short_paise: int = 0             # extra money needed to stay at the buffer the whole time


def forecast(opening_paise: int, flows: Iterable[Flow], as_of: date, *, buffer_paise: int = 0,
             cautious: bool = False) -> Forecast:
    """Day-by-day running balance from opening_paise. Within a day only the net
    counts (we cannot know the order of receipts and payments in one day)."""
    by_day: dict[date, list[int]] = {}
    for f in flows:
        if cautious and f.risky and f.amount_paise > 0:
            continue
        d = max(f.day, as_of)
        by_day.setdefault(d, [0, 0])[0 if f.amount_paise > 0 else 1] += abs(f.amount_paise)
    out = Forecast(opening_paise, low_paise=opening_paise, low_date=as_of)
    if opening_paise < buffer_paise:
        out.short_date = as_of
    running = opening_paise
    for d in sorted(by_day):
        money_in, money_out = by_day[d]
        running += money_in - money_out
        out.days.append(ForecastDay(d, money_in, money_out, running))
        if running < out.low_paise:
            out.low_paise, out.low_date = running, d
        if out.short_date is None and running < buffer_paise:
            out.short_date = d
    if out.short_date is not None:
        out.short_paise = buffer_paise - out.low_paise
    return out
