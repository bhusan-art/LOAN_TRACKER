"""Receipts and the closure certificate (Phase 10): pure helpers.

No database and no clock. services.py issues/cancels receipts; the web layer
renders them. A receipt is a snapshot taken when it is issued: the split of the
payment and the balance left, as the engine computed them on that day.
"""
from __future__ import annotations

ONES = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
        "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_100(n: int) -> str:
    if n < 20:
        return ONES[n]
    return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")


def _below_1000(n: int) -> str:
    h, rest = divmod(n, 100)
    parts = [ONES[h] + " Hundred"] if h else []
    if rest:
        parts.append(_below_100(rest))
    return " ".join(parts)


def _indian(n: int) -> str:
    """Whole number in the Indian system: crore, lakh, thousand, hundred."""
    if n == 0:
        return "Zero"
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1_000)
    parts = []
    if crore:
        parts.append(_indian(crore) + " Crore")   # above 99 crore: "One Lakh Crore"
    if lakh:
        parts.append(_below_100(lakh) + " Lakh")
    if thousand:
        parts.append(_below_100(thousand) + " Thousand")
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


def amount_in_words(paise: int) -> str:
    """322_581 -> 'Rupees Three Thousand Two Hundred Twenty-Five and Eighty-One Paise Only'."""
    if paise < 0:
        raise ValueError("amount cannot be negative")
    rupees, p = divmod(paise, 100)
    words = f"Rupees {_indian(rupees)}"
    if p:
        words += f" and {_below_100(p)} Paise"
    return words + " Only"


def receipt_number(year: int, seq: int) -> str:
    return f"RCPT-{year}-{seq:04d}"
