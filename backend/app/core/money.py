"""Integer-paisa money helpers (ARCHITECTURE §2 law 6).

All prices are stored and computed as integer paisa. Conversion to rupees happens only
at the display edge. Floats are rejected outright and sub-paisa values are never rounded
silently — an inexact amount is an error, not a guess (law 9: fail closed).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

PAISA_PER_RUPEE = 100  # definitional (₹1 = 100 paisa), not a tunable

Paisa = int


def rupees_to_paisa(rupees: Decimal | str | int) -> Paisa:
    """Convert an exact rupee amount to integer paisa.

    Accepts Decimal, decimal strings ("1234.50") or whole-rupee ints. Rejects float/bool,
    non-finite values and amounts with more than two decimal places.
    """
    if isinstance(rupees, bool | float):
        raise TypeError("rupee amounts must be Decimal, str or int — never float")
    try:
        value = rupees if isinstance(rupees, Decimal) else Decimal(rupees)
    except InvalidOperation as exc:
        raise ValueError(f"not a decimal amount: {rupees!r}") from exc
    if not value.is_finite():
        raise ValueError(f"non-finite amount: {rupees!r}")
    paisa = value * PAISA_PER_RUPEE
    if paisa != paisa.to_integral_value():
        raise ValueError(f"sub-paisa precision not representable: {rupees!r}")
    return int(paisa)


def paisa_to_rupees(paisa: Paisa) -> Decimal:
    """Exact rupee value of an integer paisa amount (display edge only)."""
    if isinstance(paisa, bool) or not isinstance(paisa, int):
        raise TypeError("paisa must be an int")
    return Decimal(paisa) / PAISA_PER_RUPEE
