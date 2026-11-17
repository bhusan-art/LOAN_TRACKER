"""Bank statement import (Phase 13): parse a bank CSV and suggest matches.

Pure: no database, no clock. services.py stores the transactions and does
whatever the owner confirms; nothing here writes money anywhere.

Reads the usual Indian bank CSV layouts: separate withdrawal/deposit columns
(SBI, HDFC, ICICI, Axis, ...) or one amount column with Dr/Cr or a sign.
Dates are always read day-first (05/02/2026 = 5 February).
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Sequence

MAX_BYTES = 2_000_000
EXISTING_WINDOW_DAYS = 3


class StatementError(ValueError):
    """The file cannot be read as a bank statement; the message says why."""


@dataclass(frozen=True)
class StatementRow:
    line_no: int
    txn_date: date
    description: str
    reference: str | None
    amount_paise: int              # + money in (credit), - money out (debit)
    balance_paise: int | None


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y", "%d.%m.%y", "%d-%b-%Y",
                "%d-%b-%y", "%d %b %Y", "%d %b %y", "%d/%b/%Y", "%d/%b/%y", "%d %B %Y", "%Y-%m-%d")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9/]+", " ", (s or "").lower()).strip()


def parse_date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    s = re.split(r"\s+\d{1,2}:\d{2}", s)[0].strip()           # drop a time part
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_money(raw: str) -> int | None:
    """'1,500.00' -> 150000; '' or '-' -> None; '(10.00)' -> -1000. Exact; no floats."""
    s = (raw or "").strip().replace(",", "").replace("₹", "").replace("INR", "").replace("Rs.", "").strip()
    if s in ("", "-", "--", "NA", "N/A"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    suffix = re.search(r"\s*(dr|cr)\.?$", s, re.I)
    if suffix:
        neg = neg or suffix.group(1).lower() == "dr"
        s = s[:suffix.start()].strip()
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise StatementError(f"not an amount: {raw!r}") from None
    if not d.is_finite():
        raise StatementError(f"not an amount: {raw!r}")
    paise = d * 100
    if paise != paise.to_integral_value():
        raise StatementError(f"amount has more than 2 decimal places: {raw!r}")
    return -int(paise) if neg else int(paise)


def _find(cells: list[str], *keys: str, avoid: tuple[str, ...] = ()) -> int | None:
    for key in keys:
        for i, c in enumerate(cells):
            if key in c and not any(a in c for a in avoid):
                return i
    return None


def _columns(header: list[str]) -> dict | None:
    cells = [_norm(c) for c in header]
    when = _find(cells, "txn date", "transaction date", "tran date", "posting date", "date", avoid=("value",))
    col = {
        "date": when if when is not None else _find(cells, "value date", "value dt"),
        "desc": _find(cells, "description", "narration", "particulars", "remarks", "details"),
        "ref": _find(cells, "ref", "cheque", "chq", "utr"),
        "debit": _find(cells, "debit", "withdrawal", "dr amount", "paid out"),
        "credit": _find(cells, "credit", "deposit", "cr amount", "paid in"),
        "amount": _find(cells, "amount", avoid=("withdrawal", "deposit", "debit", "credit")),
        "drcr": _find(cells, "dr/cr", "cr/dr", "dr cr", "cr dr", "type"),
        "balance": _find(cells, "balance"),
    }
    if col["date"] is None:
        return None
    if col["debit"] is not None and col["credit"] is not None:
        return col
    if col["amount"] is not None:
        return col
    return None


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise StatementError("the file is not text")


def parse_statement(data: bytes) -> list[StatementRow]:
    if not data or not data.strip():
        raise StatementError("the file is empty")
    if len(data) > MAX_BYTES:
        raise StatementError("the file is larger than 2 MB; export a shorter period")
    text = _decode(data)
    first = text[:4096]
    delim = max((",", ";", "\t", "|"), key=lambda d: first.count(d))
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    header_at, col = None, None
    for i, row in enumerate(rows[:60]):
        col = _columns(row)
        if col:
            header_at = i
            break
    if header_at is None:
        raise StatementError("could not find the column headings (date, narration, withdrawal/deposit or "
                             "amount). Export the statement as CSV from your bank and try again.")
    out: list[StatementRow] = []
    for n, row in enumerate(rows[header_at + 1:], start=header_at + 2):
        cell = lambda k: row[col[k]].strip() if col[k] is not None and col[k] < len(row) else ""  # noqa: E731
        when = parse_date(cell("date"))
        if when is None:
            continue                                             # footer, blank or opening-balance line
        try:
            if col["debit"] is not None and col["credit"] is not None:
                dr, cr = parse_money(cell("debit")) or 0, parse_money(cell("credit")) or 0
                amount = abs(cr) - abs(dr)
            else:
                amount = parse_money(cell("amount")) or 0
                kind = _norm(cell("drcr"))
                if kind.startswith("d") or kind in ("withdrawal",):
                    amount = -abs(amount)
                elif kind.startswith("c") or kind in ("deposit",):
                    amount = abs(amount)
            balance = parse_money(cell("balance")) if col["balance"] is not None else None
        except StatementError as exc:
            raise StatementError(f"line {n}: {exc}") from None
        if amount == 0:
            continue
        ref = cell("ref")
        out.append(StatementRow(n, when, " ".join(cell("desc").split())[:300],
                                ref if ref and ref.strip("-0 ") else None, amount, balance))
    if not out:
        raise StatementError("no transactions found under the column headings")
    return out


def fingerprints(rows: Sequence[StatementRow], account_id: int) -> list[str]:
    """Stable id per transaction so a re-upload (or an overlapping statement)
    skips rows already imported. Identical rows in one file are told apart by
    their running count, so two Rs 10 teas on one day are both kept."""
    seen: dict[tuple, int] = {}
    out = []
    for r in rows:
        key = (account_id, r.txn_date.isoformat(), r.amount_paise, _norm(r.description), r.reference or "",
               r.balance_paise)
        seen[key] = seen.get(key, 0) + 1
        out.append(hashlib.sha256(repr((*key, seen[key])).encode()).hexdigest()[:40])
    return out


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LoanInfo:
    loan_id: int
    party: str
    phone: str | None
    direction: str                 # GIVEN / TAKEN
    due_paise: int = 0             # unpaid interest + penalty today
    next_paise: int = 0            # next interest due
    payoff_paise: int = 0          # to close the loan today


@dataclass(frozen=True)
class EntryInfo:
    entry_id: int
    loan_id: int
    direction: str
    type: str                      # PAYMENT / DISBURSE / TOPUP
    entry_date: date
    amount_paise: int
    linked: bool = False           # already matched to a bank transaction


@dataclass(frozen=True)
class Suggestion:
    kind: str                      # EXISTING (link to a ledger entry) / NEW_PAYMENT (record a payment)
    loan_id: int
    entry_id: int | None
    score: int
    strong: bool
    reason: str


STOP = {"the", "and", "mr", "mrs", "ms", "shri", "smt", "sri", "demo", "bank", "ltd", "pvt", "private",
        "limited", "upi", "neft", "imps", "rtgs", "transfer", "to", "by", "from"}


def _name_hits(party: str, desc: str) -> list[str]:
    words = set(_norm(desc).replace("/", " ").split())
    return [t for t in _norm(party).replace("/", " ").split() if len(t) >= 3 and t not in STOP and t in words]


def _phone_hit(phone: str | None, desc: str) -> bool:
    digits = "".join(c for c in (phone or "") if c.isdigit())[-10:]
    return len(digits) == 10 and digits in "".join(c for c in desc if c.isdigit())


def suggest(txn_date: date, amount_paise: int, description: str, loans: Sequence[LoanInfo],
            entries: Sequence[EntryInfo], limit: int = 3) -> list[Suggestion]:
    """Best guesses for one bank transaction, best first. Money in can be a
    borrower paying you (PAYMENT on a GIVEN loan) or money you borrowed arriving
    (DISBURSE/TOPUP on a TAKEN loan); money out is the mirror image."""
    money_in = amount_paise > 0
    amt = abs(amount_paise)
    pay_dir = "GIVEN" if money_in else "TAKEN"        # whose payments look like this
    lend_dir = "TAKEN" if money_in else "GIVEN"       # whose disbursements look like this
    out: list[Suggestion] = []
    for e in entries:
        if e.linked or e.amount_paise != amt or abs((e.entry_date - txn_date).days) > EXISTING_WINDOW_DAYS:
            continue
        if (e.type == "PAYMENT" and e.direction == pay_dir) or (e.type in ("DISBURSE", "TOPUP")
                                                                 and e.direction == lend_dir):
            what = "payment" if e.type == "PAYMENT" else e.type.lower()
            out.append(Suggestion("EXISTING", e.loan_id, e.entry_id, 100, True,
                                  f"already recorded: {what} of the same amount on {e.entry_date:%d %b %Y} "
                                  f"(LN-{e.loan_id}, entry #{e.entry_id})"))
    for ln in loans:
        if ln.direction != pay_dir:
            continue
        score, why = 0, []
        names = _name_hits(ln.party, description)
        if names:
            score += 50
            why.append(f"name '{names[0]}' in the narration")
        if _phone_hit(ln.phone, description):
            score += 50
            why.append("their phone number in the narration")
        amount_hit = True
        if ln.payoff_paise and amt == ln.payoff_paise:
            score += 40
            why.append("amount closes the loan")
        elif ln.due_paise and amt == ln.due_paise:
            score += 40
            why.append("amount matches the interest due")
        elif ln.next_paise and amt == ln.next_paise:
            score += 30
            why.append("amount matches the next interest")
        else:
            amount_hit = False
        if score < 30:
            continue
        strong = score >= 80 or (amount_hit and score >= 70)
        out.append(Suggestion("NEW_PAYMENT", ln.loan_id, None, score, strong,
                              f"Record as a payment on LN-{ln.loan_id} ({ln.party}): " + ", ".join(why)))
    out.sort(key=lambda s: (-s.score, s.loan_id))
    return out[:limit]
