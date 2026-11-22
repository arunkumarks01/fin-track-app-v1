"""Derivatives support: futures and options ordering with margin checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

InstrumentType = Literal["equity", "fut", "opt_ce", "opt_pe"]


@dataclass(frozen=True)
class ContractSpec:
    """Specification for a tradable contract."""

    symbol: str
    contract_type: InstrumentType
    expiry_date: str | None = None  # YYYY-MM-DD for derivatives
    strike_paisa: int | None = None  # For options
    multiplier: int = 1  # Lot size (futures), always 1 for equity/options
    tick_size_paisa: int = 1
    margin_initial: int = 0  # Initial margin required (paisa)
    margin_maint: int = 0  # Maintenance margin (paisa)


def contract_key(symbol: str, expiry_date: str | None, strike_paisa: int | None) -> str:
    """
    Generate unique key for a contract.

    Examples:
        NSE:RELIANCE → "NSE:RELIANCE" (equity)
        NSE:NIFTY1DEC26 → "NSE:NIFTY1DEC26" (futures, expiry in symbol)
        NSE:NIFTY1DEC26C26000 → "NSE:NIFTY1DEC26C26000" (CE option)
    """
    return symbol


def margin_required(
    contract_type: InstrumentType,
    quantity: int,
    price_paisa: int,
    margin_spec: ContractSpec,
) -> int:
    """
    Calculate margin requirement for an order.

    Args:
        contract_type: 'equity', 'fut', 'opt_ce', 'opt_pe'.
        quantity: Order quantity (shares or lots).
        price_paisa: Entry price (paisa).
        margin_spec: Contract specification with margin_initial.

    Returns:
        Margin required in paisa.
    """
    if contract_type == "equity":
        # Equity margin = notional value * margin %
        # For now, assume 20% margin (BO/CO default)
        notional = quantity * price_paisa
        margin_pct_bps = 2000  # 20% = 2000 bps
        return (notional * margin_pct_bps + 5000) // 10000
    else:
        # Futures/options use spec margin
        return margin_spec.margin_initial * quantity


def sufficient_margin(
    available_margin: int,
    required_margin: int,
    buffer_paisa: int = 0,
) -> bool:
    """
    Check if sufficient margin is available for a new order.

    Args:
        available_margin: Remaining margin in account (paisa).
        required_margin: Margin required for this order (paisa).
        buffer_paisa: Safety buffer to maintain (paisa).

    Returns:
        True if available >= required + buffer.
    """
    return available_margin >= required_margin + buffer_paisa
