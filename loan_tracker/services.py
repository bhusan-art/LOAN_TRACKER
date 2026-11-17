"""Application services: the ONLY code that writes to the database.

Rules enforced here:
- ledger_entry is append-only; corrections are REVERSAL rows.
- every write recomputes the loan through the pure engine and replaces its
  `due` / `allocation` cache rows in the SAME transaction (NFR consistency).
- the engine is called with an explicit as_of date; the clock is read only
  by callers (config.today()).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, selectinload

from loan_tracker import engine as eng
from loan_tracker.db import models as m

OWNER_STATUSES = {"SETTLED", "WRITTEN_OFF"}
ENTRY_TYPES_POSTABLE = {"TOPUP", "PAYMENT", "WAIVER", "PENALTY", "RATE_CHANGE"}


class ServiceError(ValueError):
    """A request the owner can fix (shown as a message, HTTP 400)."""


# ---------------------------------------------------------------------------
# conversion to engine types
# ---------------------------------------------------------------------------
def to_terms(loan: m.Loan) -> eng.LoanTerms:
    return eng.LoanTerms(
        loan_id=loan.id,
        direction=loan.direction,
        book=loan.book,
        principal_paise=loan.principal_paise,
        start_date=loan.start_date,
        rate=Decimal(loan.rate),
        rate_unit=loan.rate_unit,
        interest_type=loan.interest_type,
        interest_cycle=loan.interest_cycle,
        due_day=loan.due_day,
        grace_days=loan.grace_days,
        penalty_rule=json.loads(loan.penalty_rule) if loan.penalty_rule else None,
        maturity_date=loan.maturity_date,
    )


def to_entries(rows: Iterable[m.LedgerEntry]) -> list[eng.LedgerEntry]:
    return [
        eng.LedgerEntry(
            entry_id=r.id,
            entry_date=r.entry_date,
            type=r.type,
            amount_paise=r.amount_paise,
            new_rate=Decimal(r.new_rate) if r.new_rate is not None else None,
            reference=r.reference,
            reverses=r.reverses_id,
        )
        for r in rows
    ]


def compute_loan(loan: m.Loan, as_of: date) -> eng.EngineResult:
    return eng.compute(to_terms(loan), to_entries(loan.entries), as_of)


# ---------------------------------------------------------------------------
# parsing helpers (forms and JSON share them)
# ---------------------------------------------------------------------------
def rupees_to_paise(value: str | int | Decimal) -> int:
    """'1,00,000.50' / 100000.5 -> 10000050 paise. Exact; no floats."""
    if isinstance(value, int):
        return value * 100
    try:
        d = Decimal(str(value).replace(",", "").replace("₹", "").strip())
    except InvalidOperation as exc:
        raise ServiceError(f"not an amount: {value!r}") from exc
    paise = d * 100
    if paise != paise.to_integral_value():
        raise ServiceError(f"amount has more than 2 decimal places: {value!r}")
    return int(paise)


def parse_rate(value: str | Decimal) -> Decimal:
    try:
        r = Decimal(str(value).strip().rstrip("%"))
    except InvalidOperation as exc:
        raise ServiceError(f"not a rate: {value!r}") from exc
    if r < 0 or r > 100:
        raise ServiceError("rate must be between 0 and 100 percent")
    return r


# ---------------------------------------------------------------------------
# parties
# ---------------------------------------------------------------------------
PARTY_FIELDS = ("name", "phone", "address", "id_proof_ref", "guarantor", "notes", "risk_tag")


def create_party(s: Session, name: str, **fields) -> m.Party:
    if not name or not name.strip():
        raise ServiceError("name is required")
    party = m.Party(name=name.strip(), **{k: v or None for k, v in fields.items() if k in PARTY_FIELDS})
    s.add(party)
    s.flush()
    return party


def update_party(s: Session, party_id: int, **fields) -> m.Party:
    party = get_party(s, party_id)
    for k, v in fields.items():
        if k in PARTY_FIELDS:
            if k == "name" and not (v or "").strip():
                raise ServiceError("name is required")
            setattr(party, k, v or None)
    s.flush()
    return party


def get_party(s: Session, party_id: int) -> m.Party:
    party = s.get(m.Party, party_id)
    if party is None:
        raise ServiceError(f"party {party_id} not found")
    return party


# ---------------------------------------------------------------------------
# loans
# ---------------------------------------------------------------------------
def get_loan(s: Session, loan_id: int, *, lock: bool = False) -> m.Loan:
    """lock=True: SELECT ... FOR UPDATE on the loan row first, so two writes to the
    same loan (phone and PC at the same moment) run one after the other and each
    rebuilds the due/allocation cache from every committed entry."""
    if lock:
        s.execute(select(m.Loan.id).where(m.Loan.id == loan_id).with_for_update())
    loan = s.scalars(
        select(m.Loan).where(m.Loan.id == loan_id)
        .options(selectinload(m.Loan.entries), selectinload(m.Loan.party))
        .execution_options(populate_existing=lock)
    ).first()
    if loan is None:
        raise ServiceError(f"loan {loan_id} not found")
    return loan


def create_loan(
    s: Session, *, party_id: int, direction: str, book: str, principal_paise: int,
    start_date: date, rate: Decimal, as_of: date, rate_unit: str = "PER_MONTH",
    interest_cycle: str = "MONTHLY", due_day: int | None = None, grace_days: int = 3,
    maturity_date: date | None = None, tenure_months: int | None = None,
    collateral: str | None = None, purpose: str | None = None,
    payment_mode: str = "CASH", reference: str | None = None,
) -> m.Loan:
    """Create the loan agreement AND its DISBURSE ledger entry in one transaction.
    DECISIONS #7: book must be chosen explicitly (no default)."""
    get_party(s, party_id)
    if direction not in ("GIVEN", "TAKEN"):
        raise ServiceError("direction must be GIVEN or TAKEN")
    if book not in ("BUSINESS", "PERSONAL"):
        raise ServiceError("choose a book: BUSINESS or PERSONAL")
    if principal_paise <= 0:
        raise ServiceError("principal must be more than zero")
    if start_date > as_of:
        raise ServiceError("start date is in the future; record the loan on the day the money moves")
    if maturity_date is not None and maturity_date <= start_date:
        raise ServiceError("maturity date must be after the start date")
    if interest_cycle == "AT_MATURITY" and maturity_date is None:
        raise ServiceError("an AT_MATURITY loan needs a maturity date")
    due_day = due_day or start_date.day
    if not 1 <= due_day <= 31:
        raise ServiceError("due day must be 1-31")
    loan = m.Loan(
        party_id=party_id, direction=direction, book=book, principal_paise=principal_paise,
        start_date=start_date, rate=parse_rate(rate), rate_unit=rate_unit, interest_type="SIMPLE",
        interest_cycle=interest_cycle, due_day=due_day, grace_days=grace_days,
        maturity_date=maturity_date, tenure_months=tenure_months, collateral=collateral or None,
        purpose=purpose or None,
    )
    s.add(loan)
    s.flush()
    s.add(m.LedgerEntry(loan_id=loan.id, entry_date=start_date, type="DISBURSE",
                        amount_paise=principal_paise, payment_mode=payment_mode,
                        reference=reference, note="loan disbursed"))
    s.flush()
    s.expire(loan, ["entries"])
    recompute_loan(s, loan, as_of)
    return loan


def post_entry(
    s: Session, loan_id: int, *, type: str, entry_date: date, as_of: date,
    amount_paise: int | None = None, new_rate: Decimal | None = None,
    payment_mode: str = "CASH", reference: str | None = None, note: str | None = None,
) -> tuple[m.LedgerEntry, eng.EngineResult]:
    """Append one ledger entry (PAYMENT, TOPUP, WAIVER, PENALTY, RATE_CHANGE)
    and recompute. Returns the entry and the loan's engine result."""
    loan = get_loan(s, loan_id, lock=True)
    if type not in ENTRY_TYPES_POSTABLE:
        raise ServiceError(f"cannot post {type} entries here")
    if loan.status in OWNER_STATUSES:
        raise ServiceError(f"loan is {loan.status}; no new entries")
    if entry_date < loan.start_date:
        raise ServiceError("entry date is before the loan start date")
    if entry_date > as_of:
        raise ServiceError("entry date is in the future")
    if type in ("PAYMENT", "TOPUP", "PENALTY") and (amount_paise is None or amount_paise <= 0):
        raise ServiceError("amount must be more than zero")
    if type == "WAIVER" and amount_paise is not None and amount_paise <= 0:
        raise ServiceError("waiver amount must be more than zero (or empty to waive the oldest due)")
    if type == "RATE_CHANGE":
        if new_rate is None:
            raise ServiceError("new rate is required")
        new_rate = parse_rate(new_rate)
        amount_paise = None
    else:
        new_rate = None
    e = m.LedgerEntry(loan_id=loan.id, entry_date=entry_date, type=type, amount_paise=amount_paise,
                      new_rate=new_rate, payment_mode=payment_mode, reference=reference or None,
                      note=note or None)
    s.add(e)
    s.flush()
    s.expire(loan, ["entries"])
    result = recompute_loan(s, loan, as_of)
    return e, result


def reverse_entry(s: Session, entry_id: int, *, entry_date: date, as_of: date,
                  note: str | None = None) -> eng.EngineResult:
    """Cancel an entry by appending a REVERSAL (never UPDATE/DELETE)."""
    original = s.get(m.LedgerEntry, entry_id)
    if original is None:
        raise ServiceError(f"entry {entry_id} not found")
    if original.type == "REVERSAL":
        raise ServiceError("a reversal cannot itself be reversed")
    if original.type == "DISBURSE":
        raise ServiceError("the disbursement cannot be reversed; mark the loan SETTLED or WRITTEN_OFF")
    get_loan(s, original.loan_id, lock=True)   # also makes the 'already reversed' check race-free
    already = s.scalar(select(m.LedgerEntry.id).where(m.LedgerEntry.reverses_id == entry_id))
    if already:
        raise ServiceError(f"entry {entry_id} is already reversed")
    if entry_date < original.entry_date:
        raise ServiceError("reversal date is before the original entry")
    s.add(m.LedgerEntry(loan_id=original.loan_id, entry_date=entry_date, type="REVERSAL",
                        reverses_id=entry_id, note=note or f"reverses entry {entry_id}"))
    s.flush()
    loan = get_loan(s, original.loan_id)
    s.expire(loan, ["entries"])
    return recompute_loan(s, loan, as_of)


def set_owner_status(s: Session, loan_id: int, status: str | None, as_of: date) -> m.Loan:
    """Owner decisions: SETTLED / WRITTEN_OFF, or None to hand status back to the engine."""
    loan = get_loan(s, loan_id, lock=True)
    if status is not None and status not in OWNER_STATUSES:
        raise ServiceError("status must be SETTLED, WRITTEN_OFF or empty")
    loan.status = status or "ACTIVE"
    s.flush()
    if status is None:
        recompute_loan(s, loan, as_of)
    return loan


def recompute_loan(s: Session, loan: m.Loan, as_of: date) -> eng.EngineResult:
    """Run the engine and replace this loan's due/allocation cache rows."""
    result = compute_loan(loan, as_of)
    entry_ids = [e.id for e in loan.entries]
    if entry_ids:
        s.execute(delete(m.Allocation).where(m.Allocation.ledger_entry_id.in_(entry_ids)))
    s.execute(delete(m.Due).where(m.Due.loan_id == loan.id))
    s.add_all(
        m.Due(loan_id=loan.id, period_start=d.due.period_start, period_end=d.due.period_end,
              due_date=d.due.due_date, interest_paise=d.due.interest_paise)
        for d in result.dues
    )
    s.add_all(
        m.Allocation(ledger_entry_id=a.entry_id, due_period_start=a.due_period_start,
                     bucket=a.bucket, amount_paise=a.amount_paise)
        for a in result.allocations
    )
    if loan.status not in OWNER_STATUSES:
        loan.status = result.loan_status
    s.flush()
    return result


def recompute_all(s: Session, as_of: date) -> int:
    loans = s.scalars(select(m.Loan).options(selectinload(m.Loan.entries))).all()
    for loan in loans:
        recompute_loan(s, loan, as_of)
    return len(loans)


# ---------------------------------------------------------------------------
# funding links
# ---------------------------------------------------------------------------
def link_funding(s: Session, *, taken_loan_id: int, given_loan_id: int, amount_paise: int,
                 link_date: date, confirm_cross_book: bool = False) -> m.FundingLink:
    taken, given = get_loan(s, taken_loan_id), get_loan(s, given_loan_id)
    if taken.direction != "TAKEN" or given.direction != "GIVEN":
        raise ServiceError("link money you BORROWED (taken loan) to money you LENT (given loan)")
    if amount_paise <= 0:
        raise ServiceError("amount must be more than zero")
    if taken.book != given.book and not confirm_cross_book:
        raise ServiceError("cross-book link (business and personal money mixed): confirm to continue")
    used_taken = sum(l.amount_paise for l in s.scalars(
        select(m.FundingLink).where(m.FundingLink.taken_loan_id == taken.id)))
    used_given = sum(l.amount_paise for l in s.scalars(
        select(m.FundingLink).where(m.FundingLink.given_loan_id == given.id)))
    if used_taken + amount_paise > taken.principal_paise:
        raise ServiceError("more than the borrowed amount would be linked")
    if used_given + amount_paise > given.principal_paise:
        raise ServiceError("more than the lent amount would be linked")
    link = m.FundingLink(taken_loan_id=taken.id, given_loan_id=given.id,
                         amount_paise=amount_paise, link_date=link_date)
    s.add(link)
    s.flush()
    return link


# ---------------------------------------------------------------------------
# read models
# ---------------------------------------------------------------------------
@dataclass
class LoanRow:
    loan: m.Loan
    result: eng.EngineResult

    @property
    def status(self) -> str:
        """Owner decisions (SETTLED / WRITTEN_OFF) win; otherwise the engine's
        status as of the computed date (never the possibly stale cached column)."""
        return self.loan.status if self.loan.status in OWNER_STATUSES else self.result.loan_status


def portfolio(s: Session, as_of: date, book: str | None = None,
              direction: str | None = None, party_id: int | None = None) -> list[LoanRow]:
    q = select(m.Loan).options(selectinload(m.Loan.entries), selectinload(m.Loan.party))
    if book:
        q = q.where(m.Loan.book == book)
    if direction:
        q = q.where(m.Loan.direction == direction)
    if party_id:
        q = q.where(m.Loan.party_id == party_id)
    return [LoanRow(loan, compute_loan(loan, as_of)) for loan in s.scalars(q.order_by(m.Loan.id)).all()]


# ---------------------------------------------------------------------------
# collections (Phase 9): contact history and promises. Never money: nothing
# here touches ledger_entry, dues or allocations.
# ---------------------------------------------------------------------------
def log_contact(s: Session, loan_id: int, *, actor: str, as_of: date, contact_date: date | None = None,
                kind: str = "CALL", outcome: str = "REACHED", note: str | None = None,
                next_follow_up: date | None = None, promise_amount_paise: int | None = None,
                promise_date: date | None = None) -> tuple[m.CollectionActivity, m.PaymentPromise | None]:
    """Record a call/visit on a loan you GAVE. outcome PROMISED needs an amount
    and a date and also creates the promise (same transaction)."""
    from loan_tracker.collections import ACTIVITY_KINDS, OUTCOMES
    loan = get_loan(s, loan_id)
    if loan.direction != "GIVEN":
        raise ServiceError("collections are for money you lent (given loans)")
    contact_date = contact_date or as_of
    if kind not in ACTIVITY_KINDS:
        raise ServiceError(f"contact type must be one of {', '.join(ACTIVITY_KINDS)}")
    if outcome not in OUTCOMES:
        raise ServiceError(f"outcome must be one of {', '.join(OUTCOMES)}")
    if contact_date > as_of:
        raise ServiceError("contact date is in the future")
    if contact_date < loan.start_date:
        raise ServiceError("contact date is before the loan start date")
    if next_follow_up is not None and next_follow_up < contact_date:
        raise ServiceError("next follow-up must be on or after the contact date")
    wants_promise = outcome == "PROMISED"
    if wants_promise:
        if not promise_amount_paise or promise_amount_paise <= 0:
            raise ServiceError("a promise needs an amount more than zero")
        if promise_date is None:
            raise ServiceError("a promise needs the date they will pay by")
        if promise_date < contact_date:
            raise ServiceError("the promised date is before the contact date")
    elif promise_amount_paise or promise_date:
        raise ServiceError("choose the outcome 'Promised to pay' to record a promise")
    act = m.CollectionActivity(loan_id=loan.id, contact_date=contact_date, kind=kind, outcome=outcome,
                               note=(note or "").strip() or None, next_follow_up=next_follow_up,
                               actor=actor or "unknown")
    s.add(act)
    s.flush()
    promise = None
    if wants_promise:
        promise = m.PaymentPromise(loan_id=loan.id, activity_id=act.id, made_on=contact_date,
                                   promised_date=promise_date, amount_paise=promise_amount_paise)
        s.add(promise)
        s.flush()
    return act, promise


def cancel_promise(s: Session, promise_id: int, note: str | None = None) -> m.PaymentPromise:
    from datetime import datetime, timezone
    p = s.get(m.PaymentPromise, promise_id)
    if p is None:
        raise ServiceError(f"promise {promise_id} not found")
    if p.cancelled_at is not None:
        raise ServiceError("this promise is already cancelled")
    p.cancelled_at = datetime.now(timezone.utc)
    p.cancel_note = (note or "").strip() or None
    s.flush()
    return p


def live_payments(loan: m.Loan) -> list[tuple[date, int]]:
    """(date, amount) of PAYMENT entries that were not reversed."""
    reversed_ids = {e.reverses_id for e in loan.entries if e.reverses_id}
    return [(e.entry_date, e.amount_paise) for e in loan.entries
            if e.type == "PAYMENT" and e.id not in reversed_ids]


def promise_state(p: m.PaymentPromise, loan: m.Loan, as_of: date):
    from loan_tracker.collections import Promise, promise_status
    return promise_status(Promise(p.made_on, p.promised_date, p.amount_paise, p.cancelled_at is not None),
                          live_payments(loan), as_of)


def loan_collections(s: Session, loan_id: int) -> tuple[list[m.CollectionActivity], list[m.PaymentPromise]]:
    acts = list(s.scalars(select(m.CollectionActivity).where(m.CollectionActivity.loan_id == loan_id)
                          .order_by(m.CollectionActivity.contact_date.desc(), m.CollectionActivity.id.desc())))
    proms = list(s.scalars(select(m.PaymentPromise).where(m.PaymentPromise.loan_id == loan_id)
                           .order_by(m.PaymentPromise.promised_date.desc(), m.PaymentPromise.id.desc())))
    return acts, proms


# ---------------------------------------------------------------------------
# audit log (Phase 8): same transaction as the change, so a failed change
# leaves no audit row and a saved change always has one
# ---------------------------------------------------------------------------
AUDIT_SECRET_KEYS = ("password", "code", "totp", "secret", "token")


def audit(s: Session, actor: str, action: str, entity: str | None = None, entity_id: int | None = None,
          ip: str | None = None, **detail) -> m.AuditLog:
    clean = {k: (v if isinstance(v, (int, bool, type(None))) else str(v)) for k, v in detail.items()
             if not any(x in k.lower() for x in AUDIT_SECRET_KEYS)}
    row = m.AuditLog(actor=actor or "unknown", action=action, entity=entity, entity_id=entity_id,
                     detail=clean, ip=ip)
    s.add(row)
    s.flush()
    return row


def audit_log(s: Session, limit: int = 200, loan_id: int | None = None) -> list[m.AuditLog]:
    """Newest first. loan_id: the loan's own rows plus its entries and links."""
    q = select(m.AuditLog)
    if loan_id is not None:
        q = q.where(or_(
            (m.AuditLog.entity == "loan") & (m.AuditLog.entity_id == loan_id),
            m.AuditLog.detail["loan_id"].astext == str(loan_id),
            m.AuditLog.detail["taken_loan_id"].astext == str(loan_id),
            m.AuditLog.detail["given_loan_id"].astext == str(loan_id)))
    return list(s.scalars(q.order_by(m.AuditLog.id.desc()).limit(limit)))


def funding_links(s: Session) -> list[m.FundingLink]:
    return list(s.scalars(select(m.FundingLink).order_by(m.FundingLink.link_date)))
