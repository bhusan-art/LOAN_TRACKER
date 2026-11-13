"""Domain model for the calculation engine.

Plain dataclasses mirroring the spec's data model (section 6). The engine
operates on these, not on database rows, so it can be unit-tested in
isolation (P7). Database-backed code maps its rows to these types.

All money fields are integer **paise** (P3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class Direction(str, Enum):
    GIVEN = "GIVEN"   # money lent to others (asset)
    TAKEN = "TAKEN"   # money borrowed from others (liability)


class Book(str, Enum):
    BUSINESS = "BUSINESS"
    PERSONAL = "PERSONAL"


class RateUnit(str, Enum):
    PER_MONTH = "PER_MONTH"
    PER_YEAR = "PER_YEAR"


class InterestType(str, Enum):
    SIMPLE = "SIMPLE"
    COMPOUND = "COMPOUND"
    EMI = "EMI"


class InterestCycle(str, Enum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    AT_MATURITY = "AT_MATURITY"


class EntryType(str, Enum):
    DISBURSE = "DISBURSE"
    TOPUP = "TOPUP"
    PAYMENT = "PAYMENT"
    PRINCIPAL_PREPAY = "PRINCIPAL_PREPAY"
    WAIVER = "WAIVER"
    PENALTY = "PENALTY"
    RATE_CHANGE = "RATE_CHANGE"
    REVERSAL = "REVERSAL"
    CAPITALISE = "CAPITALISE"


#: Entry types that increase outstanding principal.
MONEY_OUT_TYPES = frozenset({EntryType.DISBURSE, EntryType.TOPUP})


@dataclass(frozen=True, slots=True)
class PenaltyRule:
    """Per-loan late-payment penalty (spec 6.4). Disable with `enabled=False`."""

    enabled: bool = False
    kind: str = "FLAT"            # "FLAT" | "RATE"
    flat_amount_paise: int = 0    # per late cycle, when kind == "FLAT"
    extra_rate: Decimal = Decimal("0")  # per month on overdue interest, when kind == "RATE"

    @classmethod
    def none(cls) -> "PenaltyRule":
        return cls(enabled=False)


@dataclass(frozen=True, slots=True)
class Loan:
    """The agreement (P4: terms separate from events)."""

    id: int
    party_id: int
    direction: Direction
    book: Book
    principal_paise: int
    start_date: date
    rate: Decimal                 # e.g. Decimal("0.02") for 2% per month
    rate_unit: RateUnit
    interest_type: InterestType = InterestType.SIMPLE
    interest_cycle: InterestCycle = InterestCycle.MONTHLY
    due_day: int = 1
    grace_days: int = 0
    penalty_rule: PenaltyRule = field(default_factory=PenaltyRule.none)
    tenure_months: int | None = None
    maturity_date: date | None = None
    status: str = "ACTIVE"

    @property
    def monthly_rate(self) -> Decimal:
        """Normalise the rate to a per-calendar-month Decimal."""
        if self.rate_unit is RateUnit.PER_MONTH:
            return self.rate
        # PER_YEAR -> per month (÷12). Pro-rating to actual days happens at
        # accrual time via the day-count rule.
        return self.rate / Decimal(12)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One append-only event on a loan (P1/P2).

    Money is *in* the loan direction (positive): DISBURSE/TOPUP add to
    principal; PAYMENT & co. reduce it; REVERSAL undoes whatever the
    reversed event did.
    """

    id: int
    loan_id: int
    entry_date: date
    type: EntryType
    amount_paise: int            # always positive; the type supplies the sign
    payment_mode: str = "CASH"
    reference: str | None = None     # cheque/UPI ref, cheque no. etc.
    note: str | None = None
    created_at: object | None = None
    #: For REVERSAL rows: the id of the entry this reversal undoes.
    reverses_id: int | None = None
    #: For RATE_CHANGE rows: the new rate (in the loan's own rate_unit)
    #: effective from entry_date.
    new_rate: Decimal | None = None
    #: For WAIVER / PENALTY rows: the 1-based due index the entry targets
    #: (None = applies to the oldest open item, or loan-level).
    due_index: int | None = None

    @property
    def effective_amount(self) -> int:
        """Signed paise: + when the event adds principal, − when it reduces
        it. REVERSAL is the negation of the reversed event's sign."""
        if self.type in MONEY_OUT_TYPES:
            return self.amount_paise
        if self.type is EntryType.REVERSAL:
            return -self.amount_paise
        return -self.amount_paise


@dataclass(frozen=True, slots=True)
class FundingLink:
    """Which borrowed money (TAKEN) funded which loan given (GIVEN)."""

    taken_loan_id: int
    given_loan_id: int
    amount_paise: int
    link_date: date
