"""Collections (Phase 9): promise status and WhatsApp reminder links.

Pure functions, no database and no clock: services.py and reports.py pass in
the data and the as-of date. A promise is never money: its status is worked
out from real (non-reversed) PAYMENT entries only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable
from urllib.parse import quote

PROMISE_STATUSES = ("PENDING", "KEPT", "KEPT_LATE", "BROKEN", "CANCELLED")
ACTIVITY_KINDS = ("CALL", "VISIT", "WHATSAPP", "SMS", "OTHER")
OUTCOMES = ("REACHED", "NO_ANSWER", "PROMISED", "DISPUTED", "PAID", "OTHER")


@dataclass(frozen=True)
class Promise:
    made_on: date
    promised_date: date
    amount_paise: int
    cancelled: bool = False


@dataclass(frozen=True)
class PromiseState:
    status: str          # one of PROMISE_STATUSES
    paid_paise: int      # paid from made_on up to promised_date (and as_of)
    short_paise: int     # amount still missing by the promised date

    @property
    def open(self) -> bool:
        return self.status == "PENDING"


def promise_status(p: Promise, payments: Iterable[tuple[date, int]], as_of: date) -> PromiseState:
    """payments: (entry_date, amount_paise) of live PAYMENT entries on the loan.

    PENDING   promised date not passed yet and not fully paid
    KEPT      paid in full between the day it was made and the promised date
    KEPT_LATE not paid in full by the date, but in full by as_of
    BROKEN    promised date passed and not paid in full (even late)
    CANCELLED cancelled by the owner (wins over everything)"""
    payments = [(d, a) for d, a in payments if p.made_on <= d <= as_of]
    on_time = sum(a for d, a in payments if d <= p.promised_date)
    total = sum(a for _, a in payments)
    short = max(0, p.amount_paise - on_time)
    if p.cancelled:
        return PromiseState("CANCELLED", on_time, short)
    if on_time >= p.amount_paise:
        return PromiseState("KEPT", on_time, 0)
    if as_of <= p.promised_date:
        return PromiseState("PENDING", on_time, short)
    if total >= p.amount_paise:
        return PromiseState("KEPT_LATE", on_time, short)
    return PromiseState("BROKEN", on_time, short)


def wa_phone(phone: str | None, default_cc: str = "91") -> str | None:
    """Digits for wa.me: '98480 12345' -> '919848012345'. A 10-digit number
    (or 0 + 10 digits) gets the default country code; a number written with +
    keeps its own. None if it does not look like a mobile number."""
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    if phone.strip().startswith("+"):
        return digits if 8 <= len(digits) <= 15 else None
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        return default_cc + digits
    if len(digits) == 12 and digits.startswith(default_cc):
        return digits
    return None


def wa_link(phone: str | None, text: str) -> str | None:
    """Click-to-chat link that opens WhatsApp with the message typed in; the
    owner still presses send (no WhatsApp Business API, no Meta approval)."""
    number = wa_phone(phone)
    return f"https://wa.me/{number}?text={quote(text, safe='')}" if number else None


def reminder_text(name: str, loan_id: int, *, overdue_paise: int = 0, overdue_since: date | None = None,
                  next_paise: int = 0, next_date: date | None = None,
                  promise_paise: int = 0, promise_date: date | None = None) -> str:
    """Polite WhatsApp reminder for a borrower. The owner can edit it before sending."""
    from loan_tracker.web.formatting import dmy, inr
    first = name.split()[0] if name.strip() else name
    ref = f"loan LN-{loan_id}"
    if promise_paise and promise_date:
        body = f"as discussed, you will pay {inr(promise_paise)} on {ref} by {dmy(promise_date)}."
    elif overdue_paise:
        since = f" since {dmy(overdue_since)}" if overdue_since else ""
        body = (f"a reminder that {inr(overdue_paise)} interest on {ref} is pending{since}. "
                f"Kindly pay at the earliest.")
    elif next_paise and next_date:
        body = f"a reminder that {inr(next_paise)} interest on {ref} is due on {dmy(next_date)}."
    else:
        body = f"just checking in about {ref}."
    return f"Hello {first}, {body} Thank you."
