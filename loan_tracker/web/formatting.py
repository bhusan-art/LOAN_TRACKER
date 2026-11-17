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


def chart_paths(series: list[tuple[str, int, int]], width: int = 560, height: int = 200, pad: int = 12) -> dict:
    """SVG polyline points for two series (earned, cost) on a shared scale."""
    if not series:
        return {"earned": "", "cost": "", "area": "", "max": 0, "grid": []}
    top = max(max(e, c) for _, e, c in series) or 1
    n = len(series)
    step = (width - 2 * pad) / max(1, n - 1)

    def y(v: int) -> float:
        return round(height - pad - (v / top) * (height - 2 * pad), 1)

    pts_e = [(round(pad + i * step, 1), y(e)) for i, (_, e, _) in enumerate(series)]
    pts_c = [(round(pad + i * step, 1), y(c)) for i, (_, _, c) in enumerate(series)]
    line = lambda pts: " ".join(f"{x},{yy}" for x, yy in pts)
    area = f"{pts_e[0][0]},{height - pad} " + line(pts_e) + f" {pts_e[-1][0]},{height - pad}"
    grid = [round(pad + k * (height - 2 * pad) / 3, 1) for k in range(4)]
    return {"earned": line(pts_e), "cost": line(pts_c), "area": area, "max": top, "grid": grid,
            "width": width, "height": height}


def donut_style(counts: dict) -> tuple[str, list[tuple[str, int, str]]]:
    """conic-gradient for loan statuses + legend rows (label, count, css colour)."""
    order = [("ACTIVE", "var(--brand)"), ("OVERDUE", "var(--red)"), ("MATURED", "var(--orange)"),
             ("CLOSED", "var(--green)"), ("SETTLED", "#b7bfd0"), ("WRITTEN_OFF", "#8a93a6")]
    total = sum(counts.values())
    if not total:
        return "conic-gradient(var(--line) 0 100%)", []
    parts, legend, acc = [], [], 0.0
    for key, colour in order:
        c = counts.get(key, 0)
        if c:
            share = c * 100 / total
            parts.append(f"{colour} {acc:.2f}% {acc + share:.2f}%")
            acc += share
            legend.append((key.replace("_", " ").title(), c, colour))
    return "conic-gradient(" + ",".join(parts) + ")", legend
