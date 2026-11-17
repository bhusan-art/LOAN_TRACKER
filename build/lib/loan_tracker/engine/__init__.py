"""Pure calculation engine (docs/design/engine.md).

The one function later layers need is compute(terms, entries, as_of).
No database, no I/O, no clock: the same inputs always give the same output.
"""
from datetime import date
from typing import Sequence

from loan_tracker.engine.funding import mismatch_check, spread
from loan_tracker.engine.replay import replay
from loan_tracker.engine.status import aging_bucket, balances, due_status, loan_status
from loan_tracker.engine.types import (
    AccrualResult,
    Allocation,
    Balances,
    Due,
    DueStatus,
    EngineError,
    EngineResult,
    FundingLink,
    LedgerEntry,
    LoanTerms,
    MismatchFlag,
    ProjectedDue,
    SpreadReport,
)


def compute(terms: LoanTerms, entries: Sequence[LedgerEntry], as_of: date) -> EngineResult:
    """Everything about one loan as of a date: dues with status, allocation
    rows, balances (incl. payoff today), projected next due, loan status."""
    r = replay(terms, entries, as_of)
    dues = tuple(due_status(ds, as_of, terms.grace_days) for ds in r.dues)
    bal = balances(terms, r, as_of)
    return EngineResult(
        loan_id=terms.loan_id,
        as_of=as_of,
        dues=dues,
        projected_due=r.projected_due,
        allocations=tuple(r.allocations),
        balances=bal,
        loan_status=loan_status(terms, bal, dues, as_of, r.disbursed),
        segments=tuple(r.segments),
    )


def accrue(terms: LoanTerms, entries: Sequence[LedgerEntry], as_of: date) -> AccrualResult:
    """Accrual view of compute() (engine.md §2 signature)."""
    r = replay(terms, entries, as_of)
    return AccrualResult(
        periods=tuple(ds.due for ds in r.dues),
        projected_due=r.projected_due,
        interest_accrued_paise=sum(ds.due.interest_paise for ds in r.dues),
        open_period_paise=r.open_period_paise,
        segments=tuple(r.segments),
    )


__all__ = [
    "AccrualResult", "Allocation", "Balances", "Due", "DueStatus", "EngineError",
    "EngineResult", "FundingLink", "LedgerEntry", "LoanTerms", "MismatchFlag",
    "ProjectedDue", "SpreadReport", "accrue", "aging_bucket", "compute",
    "mismatch_check", "spread",
]
