"""Retention fallback for the Apache TimescaleDB build (D25).

`add_retention_policy` is TSL-only, so retention is an in-process scheduled job calling
`drop_chunks` (Apache-licensed). Whole chunks are dropped — rows newer than the cutoff are
never touched, so a chunk straddling the cutoff survives until it fully ages out.

`ohlcv_candles` is never pruned: daily history back to 2015 must survive.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from app.core.clock import Clock
from app.core.config import Settings

log = logging.getLogger(__name__)

TICKS_TABLE = "app.ticks"
CLEANUP_JOB_ID = "ticks_retention_cleanup"


async def drop_expired_chunks(conn: Any, table: str, cutoff: datetime) -> list[str]:
    """Drop every chunk of `table` lying entirely before `cutoff`. Returns dropped chunk names."""
    if cutoff.tzinfo is None:
        raise ValueError("cutoff must be timezone-aware")
    rows = await conn.fetch(
        "SELECT drop_chunks($1::regclass, older_than => $2::timestamptz) AS chunk",
        table,
        cutoff,
    )
    return [str(r["chunk"]) for r in rows]


def ticks_cleanup_job(
    pool: Any, clock: Clock, settings: Settings
) -> Callable[[], Awaitable[list[str]]]:
    retention = timedelta(days=settings.ticks_hot_days)

    async def run() -> list[str]:
        cutoff = clock.now() - retention
        async with pool.acquire() as conn:
            dropped = await drop_expired_chunks(conn, TICKS_TABLE, cutoff)
        log.info("ticks retention: dropped %d chunk(s) older than %s", len(dropped), cutoff)
        return dropped

    return run
