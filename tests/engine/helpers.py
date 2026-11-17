"""Test builders for engine tests (all values paise, dates 2026 unless stated)."""
from datetime import date
from decimal import Decimal
from itertools import count

from loan_tracker.engine.types import LedgerEntry, LoanTerms

_ids = count(1)
D = date
P = 10_000_000  # Rs 1,00,000


def terms(**over) -> LoanTerms:
    base = dict(
        loan_id=1, direction="GIVEN", book="PERSONAL", principal_paise=P,
        start_date=D(2026, 1, 1), rate=Decimal("2"), rate_unit="PER_MONTH",
        interest_type="SIMPLE", interest_cycle="MONTHLY", due_day=1,
        grace_days=3, penalty_rule=None, maturity_date=None,
    )
    base.update(over)
    return LoanTerms(**base)


def entry(type_: str, d: date, amount: int | None = None, *, new_rate=None,
          reverses: int | None = None, entry_id: int | None = None) -> LedgerEntry:
    return LedgerEntry(
        entry_id=entry_id if entry_id is not None else next(_ids),
        entry_date=d, type=type_, amount_paise=amount,
        new_rate=Decimal(new_rate) if new_rate is not None else None,
        reference=None, reverses=reverses,
    )


def disburse(t: LoanTerms, amount: int | None = None) -> LedgerEntry:
    return entry("DISBURSE", t.start_date, amount if amount is not None else t.principal_paise)


def pay(d: date, amount: int, entry_id: int | None = None) -> LedgerEntry:
    return entry("PAYMENT", d, amount, entry_id=entry_id)


def example_a():
    """Example A: Rs 1,00,000 on 1 Jan 2026 at 2%/month, due on the 1st;
    pays Rs 1,500 on 5 Feb and Rs 32,500 on 15 Mar."""
    t = terms()
    p1 = pay(D(2026, 2, 5), 150_000)
    p2 = pay(D(2026, 3, 15), 3_250_000)
    return t, [disburse(t), p1, p2], p1, p2
