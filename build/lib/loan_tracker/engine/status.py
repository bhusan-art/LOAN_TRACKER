"""Due status, loan status and balances (engine.md §1, 04.3, 04.5, 04.6).

Nothing here is stored: statuses are derived on read for any as_of date.
"""
from datetime import date, timedelta

from loan_tracker.engine.replay import DueState, ReplayResult
from loan_tracker.engine.types import Balances, DueStatus, LoanStatusName, LoanTerms

AGING_BUCKETS = ((0, 30, "0_30"), (31, 60, "31_60"), (61, 90, "61_90"), (91, None, "90_plus"))


def due_status(ds: DueState, as_of: date, grace_days: int) -> DueStatus:
    """UPCOMING -> WAIVED -> PAID -> OVERDUE -> PARTIAL (04.3).

    WAIVED is checked before PAID so a fully waived due is reachable.
    PARTIAL also covers 'nothing paid yet, still inside grace'."""
    due = ds.due
    owed = due.interest_paise + ds.penalty
    settled = ds.paid + ds.penalty_paid + ds.waived
    outstanding = max(0, owed - settled)
    grace_end = due.due_date + timedelta(days=grace_days)
    overdue_days = 0
    if due.due_date > as_of:
        status = "UPCOMING"
    elif ds.waived > 0 and ds.waived >= due.interest_paise and ds.penalty_outstanding == 0:
        status = "WAIVED"
    elif outstanding == 0:
        status = "PAID"
    elif as_of > grace_end:
        status = "OVERDUE"
        overdue_days = (as_of - grace_end).days
    else:
        status = "PARTIAL"
    return DueStatus(
        due=due,
        paid_paise=ds.paid,
        waived_paise=ds.waived,
        penalty_paise=ds.penalty,
        status=status,
        outstanding_paise=outstanding,
        overdue_days=overdue_days,
    )


def aging_bucket(overdue_days: int) -> str:
    for lo, hi, name in AGING_BUCKETS:
        if overdue_days >= lo and (hi is None or overdue_days <= hi):
            return name
    return "0_30"


def balances(terms: LoanTerms, r: ReplayResult, as_of: date) -> Balances:
    accrued = sum(ds.due.interest_paise for ds in r.dues)
    paid = sum(ds.paid for ds in r.dues)
    waived = sum(ds.waived for ds in r.dues)
    interest_out = sum(ds.interest_outstanding for ds in r.dues)
    penalty_out = r.unattached_penalty_paise + sum(ds.penalty_outstanding for ds in r.dues)
    payoff = r.principal_paise + interest_out + r.open_period_paise + penalty_out - r.credit_paise
    return Balances(
        loan_id=terms.loan_id,
        as_of=as_of,
        principal_outstanding_paise=r.principal_paise,
        interest_accrued_paise=accrued,
        interest_paid_paise=paid,
        interest_waived_paise=waived,
        interest_outstanding_paise=interest_out,
        open_period_paise=r.open_period_paise,
        penalty_outstanding_paise=penalty_out,
        credit_paise=r.credit_paise,
        payoff_today_paise=max(0, payoff),
    )


def loan_status(terms: LoanTerms, b: Balances, dues: tuple[DueStatus, ...], as_of: date,
                disbursed: bool) -> LoanStatusName:
    """CLOSED > MATURED > OVERDUE > ACTIVE. SETTLED / WRITTEN_OFF are owner
    decisions stored on the loan row, not engine outputs."""
    if disbursed and b.principal_outstanding_paise == 0 and b.interest_outstanding_paise == 0 \
            and b.penalty_outstanding_paise == 0:
        return "CLOSED"
    if terms.maturity_date is not None and as_of >= terms.maturity_date:
        return "MATURED"
    if any(d.status == "OVERDUE" for d in dues):
        return "OVERDUE"
    return "ACTIVE"
