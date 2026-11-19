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
from dataclasses import dataclass as _dataclass
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
    if not d.is_finite():
        raise ServiceError(f"not an amount: {value!r}")
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


def list_parties(s: Session) -> list[m.Party]:
    return list(s.scalars(select(m.Party).order_by(m.Party.name)))


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
    if type == "PAYMENT" and loan.direction == "GIVEN":
        issue_receipt(s, e.id, as_of)
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
    rcpt = s.scalar(select(m.Receipt).where(m.Receipt.ledger_entry_id == entry_id))
    if rcpt is not None and rcpt.cancelled_at is None:
        _cancel_receipt(rcpt, f"payment reversed on {entry_date:%d %b %Y}" + (f": {note}" if note else ""))
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
    from loan_tracker.web.formatting import inr
    if used_taken + amount_paise > taken.principal_paise:
        raise ServiceError(f"{inr(amount_paise)} is more than the {inr(taken.principal_paise - used_taken)} of LN-{taken.id}'s "
                           f"borrowed money still unlinked ({inr(used_taken)} of {inr(taken.principal_paise)} already linked)")
    if used_given + amount_paise > given.principal_paise:
        raise ServiceError(f"{inr(amount_paise)} is more than the {inr(given.principal_paise - used_given)} of LN-{given.id}'s "
                           f"lent money still unfunded ({inr(used_given)} of {inr(given.principal_paise)} already linked)")
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
# borrower statement links (Phase 15). The token is shown once; only its
# SHA-256 hash is stored. The borrower sees a small, separate view of their own
# loan (BorrowerStatement) - never the owner's notes, other loans or books.
# ---------------------------------------------------------------------------
SHARE_DAYS = (7, 30, 90)


class LinkUnavailable(ServiceError):
    """The statement link is unknown, expired or revoked (message is safe to show)."""


def _token_hash(token: str) -> str:
    import hashlib
    return hashlib.sha256(("loan-tracker share link\x00" + token).encode()).hexdigest()


def create_share_link(s: Session, loan_id: int, *, days: int, actor: str, now) -> tuple[m.ShareLink, str]:
    import secrets as _secrets
    from datetime import timedelta as _td
    loan = get_loan(s, loan_id)
    if loan.direction != "GIVEN":
        raise ServiceError("statement links are for borrowers (loans you gave)")
    if days not in SHARE_DAYS:
        raise ServiceError("a link can last 7, 30 or 90 days")
    token = _secrets.token_urlsafe(24)                    # 192 random bits
    link = m.ShareLink(token_hash=_token_hash(token), loan_id=loan.id, created_by=actor or "unknown",
                       created_at=now, expires_at=now + _td(days=days))
    s.add(link)
    s.flush()
    return link, token


def resolve_share_link(s: Session, token: str, now) -> m.ShareLink:
    import re as _re
    if not token or len(token) > 64 or not _re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise LinkUnavailable("This link is not valid.")
    link = s.scalar(select(m.ShareLink).where(m.ShareLink.token_hash == _token_hash(token)))
    if link is None:
        raise LinkUnavailable("This link is not valid.")
    if link.revoked_at is not None:
        raise LinkUnavailable("This link was switched off. Please ask for a new one.")
    if link.expires_at <= now:
        raise LinkUnavailable("This link has expired. Please ask for a new one.")
    return link


def record_share_view(s: Session, link: m.ShareLink, now) -> None:
    link.view_count += 1
    link.last_viewed_at = now
    s.flush()


def revoke_share_link(s: Session, link_id: int, *, actor: str, now) -> m.ShareLink:
    link = s.get(m.ShareLink, link_id)
    if link is None:
        raise ServiceError(f"link {link_id} not found")
    if link.revoked_at is not None:
        raise ServiceError("this link is already switched off")
    link.revoked_at, link.revoked_by = now, actor or "unknown"
    s.flush()
    return link


def share_links(s: Session, loan_id: int) -> list[m.ShareLink]:
    return list(s.scalars(select(m.ShareLink).where(m.ShareLink.loan_id == loan_id)
                          .order_by(m.ShareLink.id.desc())))


@_dataclass(frozen=True)
class BorrowerDue:
    due_date: date
    interest_paise: int
    paid_paise: int
    outstanding_paise: int
    status: str


@_dataclass(frozen=True)
class BorrowerPayment:
    entry_date: date
    amount_paise: int
    mode: str | None
    receipt_id: int | None
    receipt_number: str | None


@_dataclass(frozen=True)
class BorrowerStatement:
    """Everything a borrower may see about their own loan, and nothing else."""
    loan_id: int
    borrower: str
    start_date: date
    principal_paise: int
    rate: Decimal
    rate_unit: str
    principal_outstanding_paise: int
    interest_due_paise: int
    penalty_due_paise: int
    payoff_paise: int
    overdue_paise: int
    overdue_since: date | None
    next_due_date: date | None
    next_due_paise: int
    closed: bool
    dues: tuple
    payments: tuple


def borrower_statement(s: Session, loan_id: int, as_of: date) -> BorrowerStatement:
    loan = get_loan(s, loan_id)
    res = compute_loan(loan, as_of)
    b = res.balances
    late = [d for d in res.dues if d.status == "OVERDUE"]
    reversed_ids = {e.reverses_id for e in loan.entries if e.reverses_id}
    rcpts = {r.ledger_entry_id: r for r in s.scalars(
        select(m.Receipt).where(m.Receipt.loan_id == loan.id, m.Receipt.cancelled_at.is_(None)))}
    pays = tuple(BorrowerPayment(e.entry_date, e.amount_paise, e.payment_mode,
                                 rcpts[e.id].id if e.id in rcpts else None,
                                 rcpts[e.id].number if e.id in rcpts else None)
                 for e in sorted(loan.entries, key=lambda e: (e.entry_date, e.id), reverse=True)
                 if e.type == "PAYMENT" and e.id not in reversed_ids)
    dues = tuple(BorrowerDue(d.due.due_date, d.due.interest_paise, d.paid_paise, d.outstanding_paise, d.status)
                 for d in sorted(res.dues, key=lambda d: d.due.due_date, reverse=True)[:12])
    owner_closed = loan.status in ("SETTLED", "WRITTEN_OFF")
    nd = res.projected_due
    return BorrowerStatement(
        loan_id=loan.id, borrower=loan.party.name, start_date=loan.start_date, principal_paise=loan.principal_paise,
        rate=loan.rate, rate_unit=loan.rate_unit, principal_outstanding_paise=b.principal_outstanding_paise,
        interest_due_paise=b.interest_outstanding_paise, penalty_due_paise=b.penalty_outstanding_paise,
        payoff_paise=0 if owner_closed else b.payoff_today_paise,
        overdue_paise=sum(d.outstanding_paise for d in late), overdue_since=late[0].due.due_date if late else None,
        next_due_date=None if owner_closed or not nd else nd.due_date,
        next_due_paise=0 if owner_closed or not nd else nd.interest_paise,
        closed=owner_closed or res.loan_status == "CLOSED", dues=dues, payments=pays[:24])


# ---------------------------------------------------------------------------
# documents (Phase 14): AES-GCM encrypted files on disk, described in the
# document table. The key never touches the database.
# ---------------------------------------------------------------------------


@_dataclass(frozen=True)
class DocVault:
    directory: str
    current: bytes | None          # key used for new files
    keys: dict                     # key_id -> key, for reading (current + old)
    problem: str | None = None     # why documents are unavailable, if they are

    @classmethod
    def from_settings(cls, settings) -> "DocVault":
        from loan_tracker.docvault import key_id, parse_key
        keys, current, problem = {}, None, None
        try:
            current = parse_key(settings.document_key)
            keys[key_id(current)] = current
        except ValueError as exc:
            problem = str(exc)
        if settings.document_key_old:
            try:
                old = parse_key(settings.document_key_old)
                keys.setdefault(key_id(old), old)
            except ValueError as exc:
                problem = problem or f"DOCUMENT_KEY_OLD: {exc}"
        return cls(settings.document_dir, current, keys, problem)

    def path(self, uuid: str) -> str:
        import os
        return os.path.join(self.directory, f"{uuid}.ltd")


def store_document(s: Session, vault: DocVault, *, data: bytes, file_name: str, kind: str, title: str,
                   actor: str, party_id: int | None = None, loan_id: int | None = None) -> m.Document:
    import hashlib
    import os
    import secrets as _secrets
    from loan_tracker.docvault import KINDS, MAX_DOC_BYTES, key_id, seal, sniff
    if vault.current is None:
        raise ServiceError(f"documents are switched off: {vault.problem or 'DOCUMENT_KEY is not set'}")
    if kind not in KINDS:
        raise ServiceError("choose what kind of document this is")
    if not data:
        raise ServiceError("the file is empty")
    if len(data) > MAX_DOC_BYTES:
        raise ServiceError("the file is larger than 10 MB; scan at a lower resolution")
    found = sniff(data)
    if found is None:
        raise ServiceError("only PDF, JPEG, PNG or WebP files can be stored")
    if loan_id is not None:
        loan = get_loan(s, loan_id)
        party_id = party_id or loan.party_id
        if party_id != loan.party_id:
            raise ServiceError("that loan belongs to someone else")
    if party_id is None:
        raise ServiceError("attach the document to a person or a loan")
    if s.get(m.Party, party_id) is None:
        raise ServiceError(f"person {party_id} not found")
    title = (title or "").strip() or os.path.splitext(file_name or "")[0].strip() or kind.replace("_", " ").title()
    uuid = _secrets.token_hex(16)
    doc = m.Document(uuid=uuid, party_id=party_id, loan_id=loan_id, kind=kind, title=title[:200],
                     file_name=os.path.basename(file_name or f"document.{found[1]}")[-120:] or f"document.{found[1]}",
                     content_type=found[0], size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                     key_id=key_id(vault.current), uploaded_by=actor or "unknown")
    os.makedirs(vault.directory, mode=0o700, exist_ok=True)
    tmp = vault.path(uuid) + ".tmp"
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:   # owner-only
        f.write(seal(vault.current, data, f"document:{uuid}".encode()))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, vault.path(uuid))
    _on_rollback(s, lambda: _remove_quietly(vault.path(uuid)))   # a failed save leaves no orphan file
    s.add(doc)
    s.flush()
    return doc


def _remove_quietly(path: str) -> None:
    import os
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _on_commit(s: Session, fn) -> None:
    from sqlalchemy import event
    event.listen(s, "after_commit", lambda _s: fn(), once=True)


def _on_rollback(s: Session, fn) -> None:
    """Run fn if this transaction is rolled back instead of committed."""
    from sqlalchemy import event
    done = {"v": False}

    def committed(_s):
        done["v"] = True

    def rolled_back(_s, _prev=None):
        if not done["v"]:
            done["v"] = True
            fn()
    event.listen(s, "after_commit", committed, once=True)
    event.listen(s, "after_soft_rollback", rolled_back, once=True)


def get_document(s: Session, doc_id: int) -> m.Document:
    d = s.get(m.Document, doc_id)
    if d is None:
        raise ServiceError(f"document {doc_id} not found")
    return d


def read_document(s: Session, vault: DocVault, doc_id: int) -> tuple[m.Document, bytes]:
    import hashlib
    from loan_tracker.docvault import VaultError, open_sealed
    d = get_document(s, doc_id)
    if d.deleted_at is not None:
        raise ServiceError("this document was deleted")
    try:
        with open(vault.path(d.uuid), "rb") as f:
            blob = f.read()
    except FileNotFoundError:
        raise ServiceError("the stored file is missing; restore the documents folder from a backup") from None
    try:
        data = open_sealed(vault.keys, blob, f"document:{d.uuid}".encode())
    except VaultError as exc:
        raise ServiceError(str(exc)) from None
    if hashlib.sha256(data).hexdigest() != d.sha256:
        raise ServiceError("the stored file does not match what was uploaded")
    return d, data


def delete_document(s: Session, vault: DocVault, doc_id: int, actor: str) -> m.Document:
    """Erase the encrypted file; keep the record (who uploaded, who deleted, when)."""
    from datetime import datetime, timezone
    d = get_document(s, doc_id)
    if d.deleted_at is not None:
        raise ServiceError("this document is already deleted")
    d.deleted_at, d.deleted_by = datetime.now(timezone.utc), actor or "unknown"
    s.flush()
    path = vault.path(d.uuid)
    _on_commit(s, lambda: _remove_quietly(path))      # the file goes only once the deletion is saved
    return d


def documents(s: Session, *, party_id: int | None = None, loan_id: int | None = None,
              include_deleted: bool = False, limit: int = 500) -> list[m.Document]:
    q = select(m.Document)
    if party_id is not None:
        q = q.where(m.Document.party_id == party_id)
    if loan_id is not None:
        q = q.where(m.Document.loan_id == loan_id)
    if not include_deleted:
        q = q.where(m.Document.deleted_at.is_(None))
    return list(s.scalars(q.order_by(m.Document.id.desc()).limit(limit)))


# ---------------------------------------------------------------------------
# bank statement import (Phase 13). Money reaches the ledger only when the
# owner records a payment from a bank line (post_entry, like any payment).
# ---------------------------------------------------------------------------
def import_statement(s: Session, account_id: int, data: bytes, *, file_name: str, actor: str, as_of: date,
                     update_balance: bool = True) -> tuple[m.BankImport, m.CashBalance | None]:
    """Store the transactions of a bank CSV. Rows already imported (same
    account, same fingerprint) are skipped. Optionally set the account balance
    from the statement's last balance."""
    from loan_tracker.bankimport import StatementError, fingerprints, parse_statement
    acc = s.get(m.CashAccount, account_id)
    if acc is None:
        raise ServiceError(f"account {account_id} not found")
    if not acc.is_active:
        raise ServiceError(f"{acc.name} is closed; reopen it first")
    try:
        rows = parse_statement(data)
    except StatementError as exc:
        raise ServiceError(f"could not read the statement: {exc}") from exc
    future = [r for r in rows if r.txn_date > as_of]
    if future:
        raise ServiceError(f"line {future[0].line_no} is dated {future[0].txn_date:%d %b %Y}, in the future")
    prints = fingerprints(rows, acc.id)
    known = set(s.scalars(select(m.BankTxn.fingerprint).where(m.BankTxn.account_id == acc.id,
                                                               m.BankTxn.fingerprint.in_(prints))))
    imp = m.BankImport(account_id=acc.id, file_name=(file_name or "statement.csv")[-120:], actor=actor or "unknown",
                       row_count=len(rows), new_count=sum(1 for f in prints if f not in known),
                       period_start=min(r.txn_date for r in rows), period_end=max(r.txn_date for r in rows))
    s.add(imp)
    s.flush()
    for r, f in zip(rows, prints):
        if f not in known:
            s.add(m.BankTxn(import_id=imp.id, account_id=acc.id, fingerprint=f, line_no=r.line_no,
                            txn_date=r.txn_date, description=r.description, reference=r.reference,
                            amount_paise=r.amount_paise, balance_paise=r.balance_paise))
    s.flush()
    bal = None
    with_bal = [r for r in rows if r.balance_paise is not None]
    if update_balance and with_bal:
        ascending = rows[0].txn_date <= rows[-1].txn_date        # banks list oldest or newest first
        last_day = max(r.txn_date for r in with_bal)
        on_day = [r for r in with_bal if r.txn_date == last_day]
        last = on_day[-1] if ascending else on_day[0]
        current = latest_balances(s, include_inactive=True)
        newest = next((b.as_of for a, b in current if a.id == acc.id and b is not None), None)
        if newest is None or last.txn_date >= newest:
            bal = set_cash_balance(s, acc.id, balance_paise=last.balance_paise, on=last.txn_date, as_of=as_of,
                                   note=f"from bank statement {imp.file_name}")
    return imp, bal


def bank_imports(s: Session, limit: int = 50) -> list[m.BankImport]:
    return list(s.scalars(select(m.BankImport).order_by(m.BankImport.id.desc()).limit(limit)))


def bank_txns(s: Session, import_id: int | None = None, status: str | None = None) -> list[m.BankTxn]:
    q = select(m.BankTxn)
    if import_id is not None:
        q = q.where(m.BankTxn.import_id == import_id)
    if status:
        q = q.where(m.BankTxn.status == status)
    return list(s.scalars(q.order_by(m.BankTxn.txn_date, m.BankTxn.line_no, m.BankTxn.id)))


def _get_txn(s: Session, txn_id: int) -> m.BankTxn:
    t = s.get(m.BankTxn, txn_id)
    if t is None:
        raise ServiceError(f"bank transaction {txn_id} not found")
    return t


def _decide(t: m.BankTxn, status: str, entry_id: int | None = None, note: str | None = None) -> None:
    from datetime import datetime, timezone
    t.status, t.ledger_entry_id = status, entry_id
    t.note = (note or "").strip() or None
    t.decided_at = datetime.now(timezone.utc) if status != "NEW" else None


def match_context(s: Session, as_of: date, txns: list[m.BankTxn]):
    """LoanInfo and EntryInfo lists for bankimport.suggest()."""
    from loan_tracker.bankimport import EXISTING_WINDOW_DAYS, EntryInfo, LoanInfo
    from datetime import timedelta as td
    loans, entries = [], []
    if not txns:
        return loans, entries
    lo = min(t.txn_date for t in txns) - td(days=EXISTING_WINDOW_DAYS)
    hi = max(t.txn_date for t in txns) + td(days=EXISTING_WINDOW_DAYS)
    linked = set(s.scalars(select(m.BankTxn.ledger_entry_id).where(m.BankTxn.ledger_entry_id.is_not(None))))
    for r in portfolio(s, as_of):
        b = r.result.balances
        if r.status not in ("CLOSED", "SETTLED", "WRITTEN_OFF"):
            loans.append(LoanInfo(r.loan.id, r.loan.party.name, r.loan.party.phone, r.loan.direction,
                                  due_paise=b.interest_outstanding_paise + b.penalty_outstanding_paise,
                                  next_paise=r.result.projected_due.interest_paise if r.result.projected_due else 0,
                                  payoff_paise=b.payoff_today_paise))
        reversed_ids = {e.reverses_id for e in r.loan.entries if e.reverses_id}
        for e in r.loan.entries:
            if e.type in ("PAYMENT", "DISBURSE", "TOPUP") and e.id not in reversed_ids and lo <= e.entry_date <= hi:
                entries.append(EntryInfo(e.id, r.loan.id, r.loan.direction, e.type, e.entry_date, e.amount_paise,
                                         linked=e.id in linked))
    return loans, entries


def txn_suggestions(s: Session, as_of: date, txns: list[m.BankTxn]) -> dict[int, list]:
    from loan_tracker.bankimport import suggest
    open_txns = [t for t in txns if t.status == "NEW"]
    loans, entries = match_context(s, as_of, open_txns)
    return {t.id: suggest(t.txn_date, t.amount_paise, t.description + " " + (t.reference or ""), loans, entries)
            for t in open_txns}


def _check_direction(t: m.BankTxn, loan: m.Loan, entry_type: str) -> None:
    money_in = t.amount_paise > 0
    if entry_type == "PAYMENT":
        ok = (loan.direction == "GIVEN") == money_in
        need = "money in on a loan you gave" if loan.direction == "GIVEN" else "money out on a loan you took"
    else:
        ok = (loan.direction == "TAKEN") == money_in
        need = "money in on a loan you took" if loan.direction == "TAKEN" else "money out on a loan you gave"
    if not ok:
        raise ServiceError(f"this bank line is money {'in' if money_in else 'out'}; a {entry_type.lower()} on "
                           f"LN-{loan.id} must be {need}")


def link_bank_txn(s: Session, txn_id: int, entry_id: int) -> m.BankTxn:
    """Mark a bank line as the same money as an entry already in the ledger."""
    t = _get_txn(s, txn_id)
    if t.status != "NEW":
        raise ServiceError("this bank line is already decided; undo it first")
    e = s.get(m.LedgerEntry, entry_id)
    if e is None or e.type not in ("PAYMENT", "DISBURSE", "TOPUP"):
        raise ServiceError("choose a payment, disbursement or top-up entry")
    if s.scalar(select(m.LedgerEntry.id).where(m.LedgerEntry.reverses_id == e.id)):
        raise ServiceError(f"entry #{e.id} was reversed")
    if e.amount_paise != abs(t.amount_paise):
        raise ServiceError(f"amounts differ: bank {abs(t.amount_paise) / 100:,.2f}, entry {e.amount_paise / 100:,.2f}")
    if s.scalar(select(m.BankTxn.id).where(m.BankTxn.ledger_entry_id == e.id)):
        raise ServiceError(f"entry #{e.id} is already linked to another bank line")
    _check_direction(t, get_loan(s, e.loan_id), e.type)
    _decide(t, "LINKED", e.id)
    s.flush()
    return t


def record_bank_txn(s: Session, txn_id: int, loan_id: int, as_of: date) -> tuple[m.BankTxn, m.LedgerEntry]:
    """Record a bank line as a PAYMENT on a loan (normal waterfall; receipt for given loans)."""
    t = _get_txn(s, txn_id)
    if t.status != "NEW":
        raise ServiceError("this bank line is already decided; undo it first")
    loan = get_loan(s, loan_id)
    _check_direction(t, loan, "PAYMENT")
    e, _ = post_entry(s, loan.id, type="PAYMENT", entry_date=t.txn_date, as_of=as_of,
                      amount_paise=abs(t.amount_paise), payment_mode="BANK",
                      reference=(t.reference or f"bank line {t.id}")[:100], note=t.description[:200] or None)
    _decide(t, "RECORDED", e.id)
    s.flush()
    return t, e


def ignore_bank_txn(s: Session, txn_id: int, note: str | None = None) -> m.BankTxn:
    t = _get_txn(s, txn_id)
    if t.status != "NEW":
        raise ServiceError("this bank line is already decided; undo it first")
    _decide(t, "IGNORED", None, note)
    s.flush()
    return t


def undo_bank_txn(s: Session, txn_id: int) -> m.BankTxn:
    """Back to NEW. A RECORDED line can only be undone after its payment is
    reversed (the ledger is never edited)."""
    t = _get_txn(s, txn_id)
    if t.status == "NEW":
        raise ServiceError("nothing to undo")
    if t.status == "RECORDED" and not s.scalar(
            select(m.LedgerEntry.id).where(m.LedgerEntry.reverses_id == t.ledger_entry_id)):
        raise ServiceError(f"reverse payment #{t.ledger_entry_id} on the loan first; the ledger is never edited")
    _decide(t, "NEW")
    s.flush()
    return t


def link_exact_matches(s: Session, import_id: int, as_of: date) -> list[m.BankTxn]:
    """Link every open line of an import that has exactly one already-recorded match."""
    from dataclasses import replace as _replace
    from loan_tracker.bankimport import suggest
    txns = bank_txns(s, import_id, "NEW")
    loans, entries = match_context(s, as_of, txns)        # computed once for the whole statement
    done, used = [], set()
    for t in txns:
        avail = [_replace(e, linked=True) if e.entry_id in used else e for e in entries]
        exact = [x for x in suggest(t.txn_date, t.amount_paise, t.description, loans, avail, limit=10)
                 if x.kind == "EXISTING" and x.strong]       # never a match the narration contradicts
        if len(exact) == 1:
            link_bank_txn(s, t.id, exact[0].entry_id)
            used.add(exact[0].entry_id)
            done.append(t)
    return done


# ---------------------------------------------------------------------------
# cash and liquidity (Phase 11): accounts, balance history, planned items.
# Not loan money: nothing here touches the ledger.
# ---------------------------------------------------------------------------
CASH_KINDS = ("BANK", "CASH", "WALLET")


def create_cash_account(s: Session, *, name: str, kind: str, book: str, balance_paise: int,
                        as_of: date) -> m.CashAccount:
    name = (name or "").strip()
    if not name:
        raise ServiceError("account name is required")
    if kind not in CASH_KINDS:
        raise ServiceError("account type must be BANK, CASH or WALLET")
    if book not in ("BUSINESS", "PERSONAL"):
        raise ServiceError("book must be BUSINESS or PERSONAL")
    if s.scalar(select(m.CashAccount.id).where(m.CashAccount.name.ilike(name))):
        raise ServiceError(f"an account called '{name}' already exists")
    acc = m.CashAccount(name=name, kind=kind, book=book)
    s.add(acc)
    s.flush()
    s.add(m.CashBalance(account_id=acc.id, as_of=as_of, balance_paise=balance_paise, note="opening balance"))
    s.flush()
    return acc


def set_cash_balance(s: Session, account_id: int, *, balance_paise: int, on: date, as_of: date,
                     note: str | None = None) -> m.CashBalance:
    """Record the balance you see in the bank/wallet on a date (a new history row)."""
    acc = s.get(m.CashAccount, account_id)
    if acc is None:
        raise ServiceError(f"account {account_id} not found")
    if on > as_of:
        raise ServiceError("balance date is in the future")
    b = m.CashBalance(account_id=acc.id, as_of=on, balance_paise=balance_paise, note=(note or "").strip() or None)
    s.add(b)
    s.flush()
    return b


def set_cash_account_active(s: Session, account_id: int, active: bool) -> m.CashAccount:
    acc = s.get(m.CashAccount, account_id)
    if acc is None:
        raise ServiceError(f"account {account_id} not found")
    acc.is_active = active
    s.flush()
    return acc


def latest_balances(s: Session, book: str | None = None, include_inactive: bool = False
                    ) -> list[tuple[m.CashAccount, m.CashBalance | None]]:
    q = select(m.CashAccount).order_by(m.CashAccount.book, m.CashAccount.name)
    if book:
        q = q.where(m.CashAccount.book == book)
    if not include_inactive:
        q = q.where(m.CashAccount.is_active.is_(True))
    out = []
    for acc in s.scalars(q):
        bal = s.scalars(select(m.CashBalance).where(m.CashBalance.account_id == acc.id)
                        .order_by(m.CashBalance.as_of.desc(), m.CashBalance.id.desc()).limit(1)).first()
        out.append((acc, bal))
    return out


def balance_history(s: Session, account_id: int, limit: int = 20) -> list[m.CashBalance]:
    return list(s.scalars(select(m.CashBalance).where(m.CashBalance.account_id == account_id)
                          .order_by(m.CashBalance.as_of.desc(), m.CashBalance.id.desc()).limit(limit)))


def add_planned_item(s: Session, *, book: str, item_date: date, direction: str, amount_paise: int,
                     description: str, as_of: date) -> m.PlannedCashItem:
    description = (description or "").strip()
    if not description:
        raise ServiceError("say what the planned item is (e.g. rent, lending to Kiran)")
    if direction not in ("IN", "OUT"):
        raise ServiceError("direction must be IN or OUT")
    if book not in ("BUSINESS", "PERSONAL"):
        raise ServiceError("book must be BUSINESS or PERSONAL")
    if amount_paise is None or amount_paise <= 0:
        raise ServiceError("amount must be more than zero")
    if item_date < as_of:
        raise ServiceError("a planned item must be today or later")
    it = m.PlannedCashItem(book=book, item_date=item_date, direction=direction, amount_paise=amount_paise,
                           description=description)
    s.add(it)
    s.flush()
    return it


def cancel_planned_item(s: Session, item_id: int) -> m.PlannedCashItem:
    from datetime import datetime, timezone
    it = s.get(m.PlannedCashItem, item_id)
    if it is None:
        raise ServiceError(f"planned item {item_id} not found")
    if it.cancelled_at is not None:
        raise ServiceError("this planned item is already cancelled")
    it.cancelled_at = datetime.now(timezone.utc)
    s.flush()
    return it


def planned_items(s: Session, start: date, end: date, book: str | None = None) -> list[m.PlannedCashItem]:
    q = select(m.PlannedCashItem).where(m.PlannedCashItem.cancelled_at.is_(None),
                                        m.PlannedCashItem.item_date >= start, m.PlannedCashItem.item_date <= end)
    if book:
        q = q.where(m.PlannedCashItem.book == book)
    return list(s.scalars(q.order_by(m.PlannedCashItem.item_date, m.PlannedCashItem.id)))


# ---------------------------------------------------------------------------
# receipts (Phase 10): documents for money received on GIVEN loans
# ---------------------------------------------------------------------------
def _next_receipt_seq(s: Session, year: int) -> int:
    """Atomic: locks the year's counter row until commit, so numbers are unique
    and a rolled-back payment gives its number back (no gaps)."""
    from sqlalchemy import text as sql
    return s.execute(sql(
        "INSERT INTO receipt_counter (year, last_seq) VALUES (:y, 1) "
        "ON CONFLICT (year) DO UPDATE SET last_seq = receipt_counter.last_seq + 1 RETURNING last_seq"),
        {"y": year}).scalar_one()


def issue_receipt(s: Session, entry_id: int, issued_on: date) -> m.Receipt:
    """Receipt for one live PAYMENT on a loan you GAVE. Snapshot of the split
    and the balance left as at the payment date. Issued automatically for new
    payments; call it directly for payments recorded before receipts existed."""
    from loan_tracker.receipts import receipt_number
    e = s.get(m.LedgerEntry, entry_id)
    if e is None:
        raise ServiceError(f"entry {entry_id} not found")
    if e.type != "PAYMENT":
        raise ServiceError("receipts are only for payments")
    if s.scalar(select(m.LedgerEntry.id).where(m.LedgerEntry.reverses_id == entry_id)):
        raise ServiceError("this payment was reversed; no receipt")
    existing = s.scalar(select(m.Receipt).where(m.Receipt.ledger_entry_id == entry_id))
    if existing is not None:
        raise ServiceError(f"this payment already has receipt {existing.number}")
    loan = get_loan(s, e.loan_id)
    if loan.direction != "GIVEN":
        raise ServiceError("receipts are for money you received (given loans)")
    res = compute_loan(loan, e.entry_date)
    split = {"PENALTY": 0, "INTEREST": 0, "PRINCIPAL": 0}
    for a in res.allocations:
        if a.entry_id == e.id:
            split[a.bucket] += a.amount_paise
    seq = _next_receipt_seq(s, issued_on.year)
    r = m.Receipt(number=receipt_number(issued_on.year, seq), year=issued_on.year, seq=seq,
                  ledger_entry_id=e.id, loan_id=loan.id, issued_on=issued_on, payment_date=e.entry_date,
                  amount_paise=e.amount_paise, penalty_paise=split["PENALTY"],
                  interest_paise=split["INTEREST"], principal_paise=split["PRINCIPAL"],
                  credit_paise=e.amount_paise - sum(split.values()),
                  principal_left_paise=res.balances.principal_outstanding_paise,
                  interest_left_paise=res.balances.interest_outstanding_paise,
                  payment_mode=e.payment_mode, reference=e.reference)
    s.add(r)
    s.flush()
    return r


def _cancel_receipt(r: m.Receipt, reason: str) -> None:
    from datetime import datetime, timezone
    r.cancelled_at = datetime.now(timezone.utc)
    r.cancel_reason = reason


def payments_without_receipt(s: Session, loan_id: int | None = None) -> list[m.LedgerEntry]:
    """Live PAYMENT entries on GIVEN loans that have no receipt (recorded before Phase 10)."""
    reversed_ids = select(m.LedgerEntry.reverses_id).where(m.LedgerEntry.reverses_id.is_not(None))
    q = (select(m.LedgerEntry).join(m.Loan, m.Loan.id == m.LedgerEntry.loan_id)
         .outerjoin(m.Receipt, m.Receipt.ledger_entry_id == m.LedgerEntry.id)
         .where(m.LedgerEntry.type == "PAYMENT", m.Loan.direction == "GIVEN", m.Receipt.id.is_(None),
                m.LedgerEntry.id.not_in(reversed_ids)))
    if loan_id is not None:
        q = q.where(m.LedgerEntry.loan_id == loan_id)
    return list(s.scalars(q.order_by(m.LedgerEntry.entry_date, m.LedgerEntry.id)))


def issue_missing_receipts(s: Session, issued_on: date, loan_id: int | None = None) -> list[m.Receipt]:
    """Receipts for older payments, oldest payment first, so numbers follow payment order."""
    return [issue_receipt(s, e.id, issued_on) for e in payments_without_receipt(s, loan_id)]


def receipts(s: Session, loan_id: int | None = None, q: str = "", limit: int = 500) -> list[m.Receipt]:
    query = select(m.Receipt)
    if loan_id is not None:
        query = query.where(m.Receipt.loan_id == loan_id)
    if q.strip():
        query = query.join(m.Loan).join(m.Party).where(
            or_(m.Receipt.number.ilike(f"%{q.strip()}%"), m.Party.name.ilike(f"%{q.strip()}%")))
    return list(s.scalars(query.order_by(m.Receipt.year.desc(), m.Receipt.seq.desc()).limit(limit)))


def get_receipt(s: Session, receipt_id: int) -> m.Receipt:
    r = s.get(m.Receipt, receipt_id)
    if r is None:
        raise ServiceError(f"receipt {receipt_id} not found")
    return r


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
# users and roles (Phase 16): OWNER changes everything, VIEWER only looks.
# Managed from the command line; the web app never edits users.
# ---------------------------------------------------------------------------
ROLES = ("OWNER", "VIEWER")


def save_user(s: Session, username: str, *, password_hash: str, totp_secret: str | None,
              role: str = "OWNER") -> tuple[m.AppUser, bool]:
    """Create a user, or reset an existing one (new password and 2FA, other sessions logged out)."""
    if role not in ROLES:
        raise ServiceError("role must be OWNER or VIEWER")
    username = (username or "").strip()
    if not username:
        raise ServiceError("username is required")
    u = s.scalar(select(m.AppUser).where(m.AppUser.username == username))
    created = u is None
    if created:
        u = m.AppUser(username=username, password_hash=password_hash, totp_secret=totp_secret, role=role,
                      session_epoch=1)
        s.add(u)
    else:
        if u.role == "OWNER" and role == "VIEWER" and _active_owners(s) <= 1:
            raise ServiceError("this is the only owner; create another owner first")
        u.password_hash, u.totp_secret, u.role = password_hash, totp_secret, role
        u.session_epoch += 1                 # every existing session of this user ends
    s.flush()
    return u, created


def _active_owners(s: Session) -> int:
    from sqlalchemy import func as _f
    return s.scalar(select(_f.count()).select_from(m.AppUser)
                    .where(m.AppUser.role == "OWNER", m.AppUser.disabled_at.is_(None))) or 0


def set_user_enabled(s: Session, username: str, enabled: bool) -> m.AppUser:
    from datetime import datetime, timezone
    u = s.scalar(select(m.AppUser).where(m.AppUser.username == (username or "").strip()))
    if u is None:
        raise ServiceError(f"no user called '{username}'")
    if not enabled:
        if u.disabled_at is not None:
            raise ServiceError(f"'{u.username}' is already disabled")
        if u.role == "OWNER" and _active_owners(s) <= 1:
            raise ServiceError("this is the only owner; it cannot be disabled")
        u.disabled_at = datetime.now(timezone.utc)
        u.session_epoch += 1                 # logged out everywhere at once
    else:
        if u.disabled_at is None:
            raise ServiceError(f"'{u.username}' is not disabled")
        u.disabled_at = None
    s.flush()
    return u


def list_users(s: Session) -> list[m.AppUser]:
    return list(s.scalars(select(m.AppUser).order_by(m.AppUser.username)))


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


# ------------------------------------------------------------------ phones (Android app)
DEVICE_IDLE_DAYS = 90            # a phone not used for this long must sign in again
DEVICE_TOUCH_MINUTES = 60        # "last used" is written at most this often


def _device_hash(token: str) -> str:
    import hashlib
    return hashlib.sha256(("loan-tracker device\x00" + token).encode()).hexdigest()


def create_device(s: Session, user: m.AppUser, name: str, now) -> tuple[m.AppDevice, str]:
    """Sign a phone in. The token is returned once; only its hash is kept."""
    import secrets as _secrets
    name = " ".join((name or "").split())[:100] or "Phone"
    token = "ld_" + _secrets.token_urlsafe(32)                  # 256 random bits
    d = m.AppDevice(user_id=user.id, name=name, token_hash=_device_hash(token),
                    session_epoch=user.session_epoch, created_at=now, last_used_at=now)
    s.add(d)
    s.flush()
    return d, token


def resolve_device(s: Session, token: str, now) -> m.AppDevice | None:
    """The live device for a bearer token, or None (unknown, signed out, idle too long,
    user disabled, or password reset since sign-in)."""
    from datetime import timedelta as _td
    import re as _re
    if not token or len(token) > 100 or not _re.fullmatch(r"ld_[A-Za-z0-9_-]+", token):
        return None
    d = s.scalar(select(m.AppDevice).where(m.AppDevice.token_hash == _device_hash(token)))
    if d is None or d.revoked_at is not None:
        return None
    u = d.user
    if u.disabled_at is not None or u.session_epoch != d.session_epoch:
        return None
    last = d.last_used_at or d.created_at
    if now - last > _td(days=DEVICE_IDLE_DAYS):
        return None
    if now - last > _td(minutes=DEVICE_TOUCH_MINUTES):
        d.last_used_at = now
        s.flush()
    return d


def resolve_device_state(d: m.AppDevice, now) -> str:
    """LIVE, SIGNED_OUT, ENDED (password reset / user disabled) or IDLE (unused too long)."""
    from datetime import timedelta as _td
    if d.revoked_at is not None:
        return "SIGNED_OUT"
    if d.user.disabled_at is not None or d.user.session_epoch != d.session_epoch:
        return "ENDED"
    if now - (d.last_used_at or d.created_at) > _td(days=DEVICE_IDLE_DAYS):
        return "IDLE"
    return "LIVE"


def list_devices(s: Session, user_id: int | None = None) -> list[m.AppDevice]:
    q = select(m.AppDevice).order_by(m.AppDevice.revoked_at.is_not(None), m.AppDevice.created_at.desc())
    if user_id is not None:
        q = q.where(m.AppDevice.user_id == user_id)
    return list(s.scalars(q))


def revoke_device(s: Session, device_id: int, *, actor: str, now, user_id: int | None = None) -> m.AppDevice:
    d = s.get(m.AppDevice, device_id)
    if d is None or (user_id is not None and d.user_id != user_id):
        raise ServiceError(f"phone {device_id} not found")
    if d.revoked_at is not None:
        raise ServiceError("this phone is already signed out")
    d.revoked_at = now
    d.revoked_by = actor or "unknown"
    s.flush()
    return d
