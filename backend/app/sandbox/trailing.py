"""Trailing stop management: update SL as price moves in profit direction."""

from __future__ import annotations


def calculate_trailing_sl(
    buy_order: bool,
    highest_paisa_seen: int,
    lowest_paisa_seen: int,
    trailing_stop_bps: int,
    trailing_stop_paisa_min: int,
) -> int | None:
    """
    Calculate updated trailing SL based on price movement.

    For a buy order: as price moves up, SL trails by trailing_stop_bps.
    For a sell order: as price moves down, SL trails by trailing_stop_bps.

    Args:
        buy_order: True if this is a buy order (SL updates upward on price rise).
        highest_paisa_seen: Highest price hit so far (for buy orders).
        lowest_paisa_seen: Lowest price hit so far (for sell orders).
        trailing_stop_bps: Trailing stop distance in basis points (e.g., 500 = 5%).
        trailing_stop_paisa_min: Minimum paisa distance to trigger an SL update.

    Returns:
        New SL in paisa, or None if trailing is disabled or no update needed.
    """
    if trailing_stop_bps <= 0:
        return None

    if buy_order:
        if highest_paisa_seen <= 0:
            return None
        # Trail SL upward: SL = highest - (highest * trailing_stop_bps / 10000)
        trail_amount = max(
            (highest_paisa_seen * trailing_stop_bps + 5000) // 10000,  # Round up
            trailing_stop_paisa_min,
        )
        new_sl = highest_paisa_seen - trail_amount
        return new_sl if new_sl > 0 else None
    else:
        # Sell order: trail SL downward
        if lowest_paisa_seen <= 0:
            return None
        # Trail SL downward: SL = lowest + (lowest * trailing_stop_bps / 10000)
        trail_amount = max(
            (lowest_paisa_seen * trailing_stop_bps + 5000) // 10000,
            trailing_stop_paisa_min,
        )
        new_sl = lowest_paisa_seen + trail_amount
        return new_sl


def should_update_trailing_sl(
    current_stop_paisa: int | None,
    new_stop_paisa: int | None,
    buy_order: bool,
) -> bool:
    """
    Check if the trailing SL update should be applied.

    SL only moves in the profit direction (up for buy, down for sell).
    Never moves against the trader (down for buy, up for sell).
    """
    if current_stop_paisa is None or new_stop_paisa is None:
        return False

    if buy_order:
        # For buy: only update if new SL is higher (more profitable)
        return new_stop_paisa > current_stop_paisa
    else:
        # For sell: only update if new SL is lower (more profitable)
        return new_stop_paisa < current_stop_paisa


def update_extremes(
    buy_order: bool,
    highest_paisa_seen: int | None,
    lowest_paisa_seen: int | None,
    bar_high: int,
    bar_low: int,
) -> tuple[int, int]:
    """
    Track the highest/lowest prices seen across all bars for trailing.

    Args:
        buy_order: True if this is a buy order.
        highest_paisa_seen: Prior highest price (buy order).
        lowest_paisa_seen: Prior lowest price (sell order).
        bar_high: Today's bar high.
        bar_low: Today's bar low.

    Returns:
        (new_highest, new_lowest) for this order.
    """
    if buy_order:
        new_highest = max(highest_paisa_seen or 0, bar_high)
        return new_highest, lowest_paisa_seen or 0
    else:
        new_lowest = min(lowest_paisa_seen or bar_low, bar_low)
        return highest_paisa_seen or 0, new_lowest
