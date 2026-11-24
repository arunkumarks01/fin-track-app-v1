"""Persistence for reproducible Phase 6 historical studies."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any, cast

from app.backtest.metrics import Performance
from app.backtest.replay import Replay


async def create_run(
    conn: Any,
    *,
    strategy_id: str,
    revision: int,
    symbol: str,
    start_day: date,
    end_day: date,
    capital_paisa: int,
    assumptions: dict[str, Any],
    now: datetime,
) -> int:
    """Record immutable inputs before a runner can write a result."""
    run_id = await conn.fetchval(
        """INSERT INTO app.backtest_runs
           (strategy_id,strategy_revision,symbol,start_day,end_day,capital_paisa,assumptions,status,created_at)
           VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,'queued',$8) RETURNING id""",
        strategy_id,
        revision,
        symbol,
        start_day,
        end_day,
        capital_paisa,
        json.dumps(assumptions, sort_keys=True),
        now,
    )
    return cast(int, run_id)


async def save_result(conn: Any, run_id: int, replay: Replay, summary: Performance) -> None:
    """Atomically persist one completed replay. A run is completed exactly once."""
    async with conn.transaction():
        changed = await conn.fetchval(
            """UPDATE app.backtest_runs SET status='running'
               WHERE id=$1 AND status='queued' RETURNING id""",
            run_id,
        )
        if changed is None:
            raise ValueError("Backtest run is not queued")
        for fill in replay.fills:
            await conn.execute(
                """INSERT INTO app.backtest_fills
                   (run_id,trade_day,side,quantity,price_paisa,fees_paisa,realized_paisa)
                   VALUES($1,$2,$3,$4,$5,$6,$7)""",
                run_id,
                fill.day,
                fill.side,
                fill.quantity,
                fill.price_paisa,
                fill.fees_paisa,
                fill.realized_paisa,
            )
        await conn.executemany(
            "INSERT INTO app.backtest_equity(run_id,trade_day,equity_paisa) VALUES($1,$2,$3)",
            [
                (run_id, day, equity)
                for day, equity in zip(replay.equity_days, replay.equity_curve[1:], strict=True)
            ],
        )
        await conn.execute(
            """INSERT INTO app.backtest_metrics
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
            run_id,
            summary.ending_equity_paisa,
            summary.net_paisa,
            summary.max_drawdown.numerator,
            summary.max_drawdown.denominator,
            summary.closed_trades,
            summary.wins,
            summary.losses,
            summary.gross_profit_paisa,
            summary.gross_loss_paisa,
        )
        await conn.execute(
            """UPDATE app.backtest_runs SET status='completed',completed_at=$1 WHERE id=$2""",
            datetime.now(UTC),
            run_id,
        )
