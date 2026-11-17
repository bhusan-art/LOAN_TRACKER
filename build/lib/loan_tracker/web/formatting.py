"""Display helpers: Indian number format, dates."""
from datetime import date


def indian_group(n: int) -> str:
    """1234567 -> '12,34,567'."""
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def inr(paise: int | None, *, decimals: bool = True) -> str:
    """10_000_050 -> '₹1,00,000.50'."""
    if paise is None:
        return "—"
    sign = "−" if paise < 0 else ""
    p = abs(paise)
    out = f"{sign}₹{indian_group(p // 100)}"
    if decimals:
        out += f".{p % 100:02d}"
    return out


def rupees_input(paise: int | None) -> str:
    """Value for an <input>: 150000 -> '1500.00'."""
    if paise is None:
        return ""
    return f"{paise // 100}.{paise % 100:02d}"


def dmy(d: date | None) -> str:
    return d.strftime("%d %b %Y") if d else "—"


def pct(rate) -> str:
    return f"{rate.normalize():f}%" if rate is not None else "—"


def dshort(d: date | None) -> str:
    """'01 Nov 26' — compact date for tables on a phone."""
    return d.strftime("%d %b %y") if d else "—"
