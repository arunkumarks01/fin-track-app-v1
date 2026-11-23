"""Scheduled, stored scans. Model evaluation and on-demand requests share one path."""

from __future__ import annotations

import json
from datetime import datetime, time
from typing import Any

from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ
from app.data.candles import UnknownSymbol
from app.scoring.reports import candidate_report
from app.scoring.rules import Mode


async def scan_universe(
    pool: Any, settings: Settings, as_of: datetime, mode: Mode
) -> dict[str, Any]:
    local = as_of.astimezone(MARKET_TZ)
    if mode == "intraday" and not time(9, 15) <= local.time().replace(
        second=0, microsecond=0
    ) <= time(15):
        return {
            "skipped": "Outside the intraday scan window",
            "stopped_reason": "Outside the intraday scan window",
        }
    slot = as_of.replace(second=0, microsecond=0)
    async with pool.acquire() as conn:
        symbols = await conn.fetch(
            "SELECT symbol FROM app.universe WHERE active AND kind = 'equity' "
            "AND symbol LIKE 'NSE:%' ORDER BY symbol"
        )
        if await conn.fetchval(
            "SELECT 1 FROM app.scoring_scans WHERE mode=$1 AND as_of=$2", mode, slot
        ):
            return {
                "skipped": "This scan is already stored",
                "stopped_reason": "This scan is already stored",
            }
        if not symbols:
            return {"stopped_reason": "No active NSE equities in the universe"}
    matches = []
    unavailable = []
    for row in symbols:
        symbol = row["symbol"]
        try:
            async with (
                pool.acquire() as conn,
                conn.transaction(isolation="repeatable_read", readonly=True),
            ):
                evaluated = await candidate_report(conn, symbol, mode, slot, settings, "underlying")
        except UnknownSymbol:
            unavailable.append(symbol)
            continue
        t = evaluated["latest_bar"]
        if mode == "intraday" and (
            t is None or datetime.fromtimestamp(t, MARKET_TZ).date() != local.date()
        ):
            unavailable.append(symbol)
            continue
        if evaluated["data_status"] not in ("ok", "warming_up"):
            unavailable.append(symbol)
            continue
        qualified = [e for e in evaluated["evaluations"] if e["status"] == "matched"]
        if qualified:
            matches.append({**evaluated, "evaluations": qualified})
    report = {
        "as_of": slot.isoformat(),
        "mode": mode,
        "symbols_evaluated": len(symbols) - len(unavailable),
        "symbols_unavailable": unavailable,
        "matches": matches,
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO app.scoring_scans(mode,as_of,report)
               VALUES ($1,$2,$3::jsonb) ON CONFLICT DO NOTHING""",
            mode,
            slot,
            json.dumps(report, allow_nan=False),
        )
    return {
        "inserted": sum(len(row["evaluations"]) for row in matches),
        "progress": {
            "inserted": sum(len(row["evaluations"]) for row in matches),
            "errors": len(unavailable),
        },
    }


async def latest_scan(conn: Any, mode: Mode) -> dict[str, Any] | None:
    report = await conn.fetchval(
        "SELECT report FROM app.scoring_scans WHERE mode=$1 ORDER BY as_of DESC LIMIT 1", mode
    )
    return dict(json.loads(report)) if report else None


async def scan_history(conn: Any, mode: Mode) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT report FROM app.scoring_scans WHERE mode=$1 ORDER BY as_of DESC LIMIT 30", mode
    )
    return [dict(json.loads(row["report"])) for row in rows]
