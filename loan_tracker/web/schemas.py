"""Request bodies for the JSON API (Phase 8).

Shape and type checks only. Business rules (no future start date, cross-book
confirmation, link not larger than the loan, ...) stay in services.py so the
HTML forms and the API share them.

Money is integer paise (StrictInt: 100.5 or "100" are rejected, never rounded).
Rates are Decimal; send them as strings ("1.5") to avoid float surprises.
Unknown fields are rejected so a typo such as "princpal_paise" is not ignored.
"""
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

Paise = Annotated[StrictInt, Field(gt=0, le=10**15)]
Text = Annotated[str, Field(max_length=500)]
Rate = Annotated[Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=4)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PartyIn(_Body):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    phone: Annotated[str, Field(max_length=30)] | None = None
    address: Text | None = None
    id_proof_ref: Annotated[str, Field(max_length=100)] | None = None
    guarantor: Annotated[str, Field(max_length=200)] | None = None
    notes: Annotated[str, Field(max_length=2000)] | None = None
    risk_tag: Annotated[str, Field(max_length=50)] | None = None


class LoanIn(_Body):
    party_id: Annotated[StrictInt, Field(gt=0)]
    direction: Literal["GIVEN", "TAKEN"]
    book: Literal["BUSINESS", "PERSONAL"]
    principal_paise: Paise
    rate: Rate
    start_date: date | None = None          # default: today
    rate_unit: Literal["PER_MONTH", "PER_YEAR"] = "PER_MONTH"
    interest_cycle: Literal["MONTHLY", "QUARTERLY", "AT_MATURITY"] = "MONTHLY"
    due_day: Annotated[StrictInt, Field(ge=1, le=31)] | None = None
    grace_days: Annotated[StrictInt, Field(ge=0, le=90)] = 3
    maturity_date: date | None = None
    purpose: Text | None = None
    collateral: Text | None = None
    payment_mode: Literal["CASH", "UPI", "BANK"] = "CASH"
    reference: Annotated[str, Field(max_length=100)] | None = None


class EntryIn(_Body):
    type: Literal["TOPUP", "PAYMENT", "WAIVER", "PENALTY", "RATE_CHANGE"]
    entry_date: date | None = None          # default: today
    amount_paise: Paise | None = None       # required for every type except RATE_CHANGE (services checks)
    new_rate: Rate | None = None            # RATE_CHANGE only
    payment_mode: Literal["CASH", "UPI", "BANK"] = "CASH"
    reference: Annotated[str, Field(max_length=100)] | None = None
    note: Text | None = None


class ReverseIn(_Body):
    note: Text | None = None


class FundingIn(_Body):
    taken_loan_id: Annotated[StrictInt, Field(gt=0)]
    given_loan_id: Annotated[StrictInt, Field(gt=0)]
    amount_paise: Paise
    link_date: date | None = None           # default: today
    confirm_cross_book: bool = False


class ContactIn(_Body):
    kind: Literal["CALL", "VISIT", "WHATSAPP", "SMS", "OTHER"] = "CALL"
    outcome: Literal["REACHED", "NO_ANSWER", "PROMISED", "DISPUTED", "PAID", "OTHER"] = "REACHED"
    contact_date: date | None = None        # default: today
    note: Annotated[str, Field(max_length=2000)] | None = None
    next_follow_up: date | None = None
    promise_amount_paise: Paise | None = None   # outcome PROMISED only
    promise_date: date | None = None             # outcome PROMISED only


SignedPaise = Annotated[StrictInt, Field(ge=-10**15, le=10**15)]


class CashAccountIn(_Body):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    kind: Literal["BANK", "CASH", "WALLET"] = "BANK"
    book: Literal["BUSINESS", "PERSONAL"]
    balance_paise: SignedPaise = 0          # negative = overdraft


class CashBalanceIn(_Body):
    balance_paise: SignedPaise
    on: date | None = None                  # default: today
    note: Text | None = None


class PlannedIn(_Body):
    book: Literal["BUSINESS", "PERSONAL"]
    item_date: date
    direction: Literal["IN", "OUT"]
    amount_paise: Paise
    description: Annotated[str, Field(min_length=1, max_length=200)]


def describe(errors: list[dict]) -> str:
    """One readable line from pydantic errors: 'book: Input should be ...; rate: ...'."""
    parts = []
    for e in errors:
        loc = ".".join(str(x) for x in e.get("loc", ()) if x != "body") or "body"
        parts.append(f"{loc}: {e.get('msg', 'invalid')}")
    return "; ".join(parts)
