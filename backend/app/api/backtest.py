"""Historical study controls and persisted results; mutations use the global bearer gate."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.backtest.service import run_study
from app.backtest.strategies import DAILY_LONG_BASELINES

router = APIRouter(prefix="/api/backtest")


class StudyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifier: str
    symbol: str = Field(pattern=r"^NSE:[A-Z0-9&_.-]{1,64}$")
    start_day: date
    end_day: date
    capital_paisa: int = Field(gt=0, le=10**12, strict=True)
    quantity: int = Field(ge=1, le=1_000_000, strict=True)


def decode(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in ("assumptions", "result_state"):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result


@router.get("/strategies")
async def strategies() -> dict[str, Any]:
    return {"strategies": [asdict(spec) for spec in DAILY_LONG_BASELINES]}


@router.get("/runs")
async def runs(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    async with request.app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT r.*,m.ending_equity_paisa,m.net_paisa,m.closed_trades,m.wins,m.losses,
                      m.max_drawdown_numerator,m.max_drawdown_denominator,
                      m.gross_profit_paisa,m.gross_loss_paisa
               FROM app.backtest_runs r LEFT JOIN app.backtest_metrics m ON m.run_id=r.id
               ORDER BY r.id DESC LIMIT $1""",
            limit,
        )
    return {"runs": [decode(row) for row in rows]}


@router.get("/runs/{run_id}")
async def result(request: Request, run_id: int) -> dict[str, Any]:
    async with (
        request.app.state.pool.acquire() as conn,
        conn.transaction(isolation="repeatable_read", readonly=True),
    ):
        row = await conn.fetchrow("SELECT * FROM app.backtest_runs WHERE id=$1", run_id)
        if row is None:
            raise HTTPException(404, "Backtest run not found")
        metrics = await conn.fetchrow("SELECT * FROM app.backtest_metrics WHERE run_id=$1", run_id)
        fills = await conn.fetch(
            "SELECT * FROM app.backtest_fills WHERE run_id=$1 ORDER BY id",
            run_id,
        )
        equity = await conn.fetch(
            """SELECT trade_day,equity_paisa FROM app.backtest_equity
               WHERE run_id=$1 ORDER BY trade_day""",
            run_id,
        )
    return {
        "run": decode(row),
        "metrics": dict(metrics) if metrics else None,
        "fills": [dict(f) for f in fills],
        "equity": [dict(e) for e in equity],
    }


@router.post("/runs", status_code=201)
async def create(request: Request, data: StudyInput) -> dict[str, Any]:
    try:
        async with request.app.state.pool.acquire() as conn:
            if not await conn.fetchval("SELECT 1 FROM app.universe WHERE symbol=$1", data.symbol):
                raise ValueError("Symbol is not in the stored universe")
            run_id = await run_study(
                conn,
                **data.model_dump(),
                settings=request.app.state.settings,
                now=request.app.state.clock.now(),
            )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return await result(request, run_id)
