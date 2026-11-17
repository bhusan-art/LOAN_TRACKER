"""The only rounding primitive in the engine (engine.md §4.1, DECISIONS #8).

Money is integer paise; exact accrual math is Decimal. Round half-up to
whole paise, at the two defined points only (due generation, open period).
"""
from decimal import ROUND_HALF_UP, Decimal

_PAISE = Decimal("1")


def round_half_up(x: Decimal) -> int:
    """Quantize x to 1 paise with ROUND_HALF_UP; return the int paise value.

    The ONLY rounding primitive in the engine — no whole-rupee option (#8).
    """
    return int(x.quantize(_PAISE, rounding=ROUND_HALF_UP))
