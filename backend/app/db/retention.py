"""Tick retention safeguards for the Apache TimescaleDB build (D25).

`add_retention_policy` is TSL-only. The low-level `drop_chunks` helper is available for
explicit maintenance, but must not be used as an unverified scheduled retention policy.

Automatic deletion is suspended until the verified Parquet export is restored. Historical
data must not be discarded solely because it is older than a retention cutoff.
`ohlcv_candles` is never pruned: daily history back to 2015 must survive.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
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
    async def run() -> list[str]:
        log.warning("Tick cleanup suspended: verified Parquet export is unavailable")
        return []

    return run
