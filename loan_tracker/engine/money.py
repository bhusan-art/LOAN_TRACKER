"""Exact-money primitives (principle P3).

All amounts in the engine are integer paise. The only place rounding is allowed
to happen is :func:`half_up_paise`, applied once per due/accrual point.
Floats never touch a money value in this package.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_ONE_PAISE = Decimal("0.01")
_HUNDRED = Decimal(100)


def to_paise(amount: Decimal) -> int:
    """Convert a rupee Decimal to integer paise, rejecting non-exact values."""
    paise = amount * _HUNDRED
    if paise != paise.to_integral_value(rounding=ROUND_HALF_UP):
        raise ValueError(f"amount has sub-paise precision: {amount}")
    return int(paise)


def half_up_paise(paise: Decimal) -> int:
    """Round paise (possibly fractional) to integer paise, half-up.

    This is the single defined rounding point the spec calls for: "Round only
    at defined points (per due, half-up to the paisa)".
    """
    return int(paise.to_integral_value(rounding=ROUND_HALF_UP))


def paise_to_rupees(paise: int) -> Decimal:
    """Integer paise -> rupee Decimal (e.g. 1671 -> 16.71). For display only."""
    return Decimal(paise) / _HUNDRED


def fmt_inr(paise: int, symbol: str = "₹") -> str:
    """Format integer paise in Indian digit grouping: 10567100 -> ₹1,05,671.00."""
    rupees_abs = abs(paise)
    sign = "-" if paise < 0 else ""
    whole = rupees_abs // 100
    frac = rupees_abs % 100
    s = str(whole)
    if len(s) <= 3:
        grouped = s
    else:
        last3 = s[-3:]
        rest = s[:-3]
        groups = []
        while len(rest) > 2:
            groups.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.append(rest)
        groups.reverse()
        grouped = groups[0] + "," + ",".join(groups) + last3
    return f"{sign}{symbol}{grouped}.{frac:02d}"
