"""Automatic exit detection: SL and target hit for filled paper orders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.sandbox.model import Bar, Instruction

ExitReason = Literal["stop_hit", "target_hit"]


@dataclass(frozen=True)
class ExitTrigger:
    """An exit condition was met."""

    reason: ExitReason
    price: int


def detect_exit(
    order: Instruction,
    bar: Bar,
    filled_qty: int,
    current_stop_paisa: int | None = None,
) -> ExitTrigger | None:
    """
    Check if a filled order's SL or target was hit in the given bar.

    Args:
        order: The filled buy/sell order with risk_stop_paisa and/or risk_target_paisa.
        bar: The day's OHLC bar.
        filled_qty: Quantity filled (> 0); if 0, no exit check.
        current_stop_paisa: Live SL (for trailing stops); if None, use order.risk_stop_paisa.

    Returns:
        ExitTrigger if SL or target hit, else None.
        SL is checked first; if hit, target is ignored.
    """
    if filled_qty <= 0:
        return None

    buy = order.side == "buy"
    sl = current_stop_paisa if current_stop_paisa is not None else order.risk_stop_paisa

    # Stop hit (checked first).
    if sl is not None:
        if buy and bar.low <= sl:
            return ExitTrigger("stop_hit", price=max(bar.open, sl))
        elif not buy and bar.high >= sl:
            return ExitTrigger("stop_hit", price=min(bar.open, sl))

    # Target hit (only if no stop).
    target = order.risk_target_paisa
    if target is not None:
        if buy and bar.high >= target:
            return ExitTrigger("target_hit", price=min(bar.open, target))
        elif not buy and bar.low <= target:
            return ExitTrigger("target_hit", price=max(bar.open, target))

    return None


def exit_instruction(
    original: Instruction,
    trigger: ExitTrigger,
) -> Instruction:
    """
    Generate an exit order from a filled order and a trigger.

    Exit is always MARKET, same symbol, reverse side, same quantity.
    Exit price is embedded in the trigger (actual price will be matched in bar replay).
    """
    if original.quantity <= 0:
        raise ValueError("Cannot exit with quantity <= 0")

    return Instruction(
        symbol=original.symbol,
        side="sell" if original.side == "buy" else "buy",
        kind="market",
        quantity=original.quantity,
        limit_paisa=None,
        trigger_paisa=None,
        risk_stop_paisa=None,
        risk_target_paisa=None,
    )


def realized_pnl(
    buy_price_paisa: int,
    sell_price_paisa: int,
    quantity: int,
    fees_buy: int,
    fees_sell: int,
) -> int:
    """
    Calculate realized P&L for a closed position (buy then sell).

    Returns integer paisa (can be negative).
    """
    gross = (sell_price_paisa - buy_price_paisa) * quantity
    net = gross - fees_buy - fees_sell
    return net
