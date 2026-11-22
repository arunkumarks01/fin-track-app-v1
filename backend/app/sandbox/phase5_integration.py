"""Phase 5 integration: wire auto-exits, trailing stops, live marks into store.advance()."""

from __future__ import annotations

from typing import Any

from app.sandbox.exits import detect_exit, exit_instruction
from app.sandbox.marks import calculate_mtm, stale_mark_age_seconds
from app.sandbox.model import Bar, Instruction
from app.sandbox.trailing import (
    calculate_trailing_sl,
    should_update_trailing_sl,
    update_extremes,
)


async def process_exits_and_marks(
    conn: Any,
    order: dict[str, Any],
    bar: Bar,
    symbol: str,
    position: dict[str, Any] | None,
    filled_qty: int,
    now_unix: float,
) -> tuple[bool, dict[str, Any] | None]:
    """
    Check if a filled order should auto-exit, update position marks.

    Returns:
        (should_exit: bool, exit_order: dict or None)
    """
    if position is None or filled_qty <= 0:
        return False, None

    instruction = Instruction.model_validate_json(order["instruction"])

    # Update highest/lowest prices for trailing stops
    new_highest, new_lowest = update_extremes(
        buy_order=instruction.side == "buy",
        highest_paisa_seen=order.get("highest_paisa_seen"),
        lowest_paisa_seen=order.get("lowest_paisa_seen"),
        bar_high=bar.high,
        bar_low=bar.low,
    )
    if new_highest > 0 or new_lowest > 0:
        await conn.execute(
            """UPDATE app.paper_orders
               SET highest_paisa_seen=$1, lowest_paisa_seen=$2 WHERE id=$3""",
            new_highest,
            new_lowest,
            order["id"],
        )

    # Calculate trailing SL if enabled
    current_stop = order.get("current_stop_paisa") or instruction.risk_stop_paisa
    if order.get("trailing_stop_bps", 0) > 0:
        new_trailing_sl = calculate_trailing_sl(
            buy_order=instruction.side == "buy",
            highest_paisa_seen=new_highest,
            lowest_paisa_seen=new_lowest,
            trailing_stop_bps=order.get("trailing_stop_bps", 0),
            trailing_stop_paisa_min=order.get("trailing_stop_paisa_min", 0),
        )
        if new_trailing_sl and should_update_trailing_sl(
            current_stop, new_trailing_sl, instruction.side == "buy"
        ):
            current_stop = new_trailing_sl
            await conn.execute(
                "UPDATE app.paper_orders SET current_stop_paisa=$1 WHERE id=$2",
                new_trailing_sl,
                order["id"],
            )

    # Detect exit (SL or target hit)
    trigger = detect_exit(instruction, bar, filled_qty, current_stop_paisa=current_stop)
    if trigger:
        exit_ord = exit_instruction(instruction, trigger)
        return True, exit_ord.model_dump()

    # Update position mark price
    mark_updated_at = position.get("mark_updated_at")
    if mark_updated_at is None or stale_mark_age_seconds(
        mark_updated_at.timestamp() if hasattr(mark_updated_at, "timestamp") else 0,
        now_unix,
    ) > 300:  # Update mark if stale (> 5 min)
        cost_per = position["cost_paisa"] // position["quantity"]
        mtm = calculate_mtm(bar.close, cost_per, position["quantity"])
        await conn.execute(
            """UPDATE app.paper_positions
               SET mark_paisa=$1, mark_updated_at=now(), mtm_paisa=$2
               WHERE account_id=$3 AND symbol=$4""",
            bar.close,
            mtm,
            order["account_id"],
            symbol,
        )

    return False, None
