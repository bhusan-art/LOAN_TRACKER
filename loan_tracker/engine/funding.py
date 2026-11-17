"""Spread and cash-flow mismatch across loans (04.7, 04.8).

Works on the outputs of compute() for each loan, so dues and dates come from
the same engine for GIVEN and TAKEN loans (P6).
"""
from datetime import date
from decimal import Decimal
from typing import Mapping, Sequence

from loan_tracker.engine.money import round_half_up
from loan_tracker.engine.types import (
    EngineResult,
    FundingLink,
    LinkSpread,
    LoanTerms,
    MismatchFlag,
    SpreadReport,
)

ADVICE = "Set borrowers' due days a few days BEFORE your own payable due days."


def monthly_rate(t: LoanTerms) -> Decimal:
    """Rate as % per month (PER_YEAR / 12)."""
    return t.rate if t.rate_unit == "PER_MONTH" else t.rate / 12


def spread(
    links: Sequence[FundingLink],
    terms: Mapping[int, LoanTerms],
    taken_principal: Mapping[int, int] | None = None,
) -> SpreadReport:
    """Monthly spread per funding link, negative spreads, and idle cost.

    spread = link amount x (given rate - taken rate) / 100, both per month.
    Idle cost = borrowed principal not linked to any given loan x its rate.
    taken_principal: current outstanding principal per TAKEN loan id
    (defaults to the original principal on the terms)."""
    out: list[LinkSpread] = []
    linked: dict[int, int] = {}
    for link in links:
        g, t = terms[link.given_loan_id], terms[link.taken_loan_id]
        if g.direction != "GIVEN" or t.direction != "TAKEN":
            raise ValueError(f"funding link {link}: needs a TAKEN source and a GIVEN target")
        value = round_half_up(Decimal(link.amount_paise) * (monthly_rate(g) - monthly_rate(t)) / 100)
        out.append(LinkSpread(link, value, value < 0, g.book != t.book))
        linked[link.taken_loan_id] = linked.get(link.taken_loan_id, 0) + link.amount_paise
    idle = Decimal(0)
    for loan_id, t in terms.items():
        if t.direction != "TAKEN":
            continue
        principal = (taken_principal or {}).get(loan_id, t.principal_paise)
        unlinked = max(0, principal - linked.get(loan_id, 0))
        idle += Decimal(unlinked) * monthly_rate(t) / 100
    return SpreadReport(
        links=tuple(out),
        negative_spread=tuple(s for s in out if s.negative),
        idle_cost_paise_per_cycle=round_half_up(idle),
    )


def _next_due_date(r: EngineResult) -> date | None:
    unpaid = [d.due.due_date for d in r.dues if d.outstanding_paise > 0]
    if unpaid:
        return min(unpaid)
    return r.projected_due.due_date if r.projected_due else None


def mismatch_check(
    links: Sequence[FundingLink],
    results: Mapping[int, EngineResult],
    written_off: frozenset[int] = frozenset(),
) -> list[MismatchFlag]:
    """For each funded TAKEN loan T (next payable date D):
    - linked GIVEN G whose next receivable date is after D -> WARNING
    - linked G with an OVERDUE due -> RISK (T's interest comes from own funds)
    - linked G written off -> EXPOSURE (T's remaining principal)"""
    flags: list[MismatchFlag] = []
    for link in links:
        t_res = results.get(link.taken_loan_id)
        g_res = results.get(link.given_loan_id)
        if t_res is None or g_res is None or t_res.loan_status == "CLOSED":
            continue
        pay_date = _next_due_date(t_res)
        if link.given_loan_id in written_off:
            flags.append(MismatchFlag(
                link.taken_loan_id, link.given_loan_id, "EXPOSURE",
                f"Funded loan written off: {t_res.balances.principal_outstanding_paise} paise "
                f"still owed on borrowed loan {link.taken_loan_id}.",
            ))
            continue
        recv_date = _next_due_date(g_res)
        if pay_date and recv_date and recv_date > pay_date:
            flags.append(MismatchFlag(
                link.taken_loan_id, link.given_loan_id, "WARNING",
                f"Payable before receivable: you pay on {pay_date}, you receive on {recv_date}. {ADVICE}",
            ))
        if any(d.status == "OVERDUE" for d in g_res.dues):
            interest = t_res.projected_due.interest_paise if t_res.projected_due else 0
            flags.append(MismatchFlag(
                link.taken_loan_id, link.given_loan_id, "RISK",
                f"Borrower overdue: next {interest} paise on borrowed loan "
                f"{link.taken_loan_id} must come from own funds.",
            ))
    return flags
