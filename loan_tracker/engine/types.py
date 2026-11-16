"""Engine data types (docs/design/engine.md §1).

Frozen dataclasses only — no logic, no I/O. Money = int paise; rates =
Decimal; dates = datetime.date.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

Direction = Literal["GIVEN", "TAKEN"]
Book = Literal["BUSINESS", "PERSONAL"]
RateUnit = Literal["PER_MONTH", "PER_YEAR"]
InterestType = Literal["SIMPLE", "COMPOUND", "EMI"]
InterestCycle = Literal["MONTHLY", "QUARTERLY", "AT_MATURITY"]
EntryType = Literal[
    "DISBURSE", "TOPUP", "PAYMENT", "WAIVER",
    "PENALTY", "RATE_CHANGE", "CAPITALISE", "REVERSAL",
]
DueStatusName = Literal["UPCOMING", "PARTIAL", "PAID", "OVERDUE", "WAIVED"]
AllocationBucket = Literal["PENALTY", "INTEREST", "PRINCIPAL"]
MismatchKind = Literal["WARNING", "RISK", "EXPOSURE"]


@dataclass(frozen=True)
class LoanTerms:
    loan_id: int
    direction: Direction
    book: Book
    principal_paise: int          # initial disbursed principal, paise
    start_date: date             # disbursement day; interest counts from this day (04.1)
    rate: Decimal                # initial rate, percent (Decimal("2") = 2%/month)
    rate_unit: RateUnit
    interest_type: InterestType  # v1: SIMPLE only (DECISIONS #1)
    interest_cycle: InterestCycle
    due_day: int                 # 1..31; clamped to month-end at cycle boundaries (§4.2)
    grace_days: int              # default 3 (#9)
    penalty_rule: dict | None    # JSON {"kind","paise","per"}; off in v1 (#3)
    maturity_date: date | None   # None = open-ended; at maturity -> MATURED (#12)


@dataclass(frozen=True)
class LedgerEntry:
    entry_id: int
    entry_date: date
    type: EntryType
    amount_paise: int | None     # money types: > 0; RATE_CHANGE/REVERSAL: None
    new_rate: Decimal | None     # RATE_CHANGE only (review 2.4)
    reference: str | None
    reverses: int | None         # on a REVERSAL row: the entry_id it reverses (#13)


@dataclass(frozen=True)
class FundingLink:
    taken_loan_id: int
    given_loan_id: int
    amount_paise: int
    link_date: date


@dataclass(frozen=True)
class Due:
    loan_id: int
    period_start: date           # identity key with loan_id (== due_date - period)
    period_end: date            # last day the period accrues (== due_date - 1)
    due_date: date
    interest_paise: int         # rounded at generation (§4.1-a); immutable afterwards


@dataclass(frozen=True)
class DueStatus:
    due: Due
    paid_paise: int             # sum of INTEREST allocations to this due, live entries
    waived_paise: int          # sum of WAIVER effects on this due
    penalty_paise: int         # PENALTY entries posted for this cycle (0 if off)
    status: DueStatusName
    outstanding_paise: int      # interest + penalty - paid - waived (>= 0)
    overdue_days: int           # 0 unless status == OVERDUE; days past due_date + grace


@dataclass(frozen=True)
class ProjectedDue:
    loan_id: int
    period_start: date
    period_end: date
    due_date: date
    interest_paise: int         # current rate/principal held flat to due_date; informational


@dataclass(frozen=True)
class Allocation:
    entry_id: int              # the live PAYMENT entry
    due_period_start: date | None   # references a due by period_start (None: PRINCIPAL & PENALTY)
    bucket: AllocationBucket
    amount_paise: int          # > 0


@dataclass(frozen=True)
class Balances:
    loan_id: int
    as_of: date
    principal_outstanding_paise: int
    interest_accrued_paise: int      # REALIZED basis: rounded due interests, due_date <= as_of
    interest_paid_paise: int
    interest_waived_paise: int
    interest_outstanding_paise: int  # accrued - paid - waived
    open_period_paise: int           # in-progress tail (#11); 0 on a boundary or principal = 0
    penalty_outstanding_paise: int
    credit_paise: int
    payoff_today_paise: int          # principal + outstd + open + penalty - credit, >= 0 (#11)


@dataclass(frozen=True)
class LinkSpread:
    link: FundingLink
    spread_paise_per_cycle: int   # link amount * (rate_given - rate_taken), PER_MONTH-normalised
    negative: bool
    cross_book: bool              # books of the two loans differ (#7)


@dataclass(frozen=True)
class SpreadReport:
    links: tuple[LinkSpread, ...]
    negative_spread: tuple[LinkSpread, ...]
    idle_cost_paise_per_cycle: int  # unlinked TAKEN principal * its PER_MONTH rate (4.7)


@dataclass(frozen=True)
class MismatchFlag:
    taken_loan_id: int
    given_loan_id: int | None
    kind: MismatchKind
    message: str


@dataclass(frozen=True)
class Segment:
    from_day: date
    to_day: date                # inclusive; segments split only at rate/principal
                                # event days (DECISIONS #15 — no month-edge splits)
    principal_paise: int
    rate: Decimal               # effective rate over this segment's days
    days: int
    interest_exact: Decimal     # unrounded paise (exact Decimal); rounded only at a
                                # generation point (§4.1)


@dataclass(frozen=True)
class AccrualResult:
    periods: tuple[Due, ...]              # realized dues; interest rounded at generation (§4.1-a)
    projected_due: ProjectedDue | None
    interest_accrued_paise: int           # REALIZED basis (due_date <= as_of)
    open_period_paise: int               # in-progress tail for payoff (#11)
    segments: tuple[Segment, ...]        # audit aid; see Segment


@dataclass(frozen=True)
class EngineResult:
    loan_id: int
    as_of: date
    dues: tuple[DueStatus, ...]        # realized dues (due_date <= as_of), keyed by period_start
    projected_due: ProjectedDue | None  # None only when principal fully repaid (#12)
    allocations: tuple[Allocation, ...]
    balances: Balances
