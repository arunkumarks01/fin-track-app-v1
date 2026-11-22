"""Retention fallback for the Apache TimescaleDB build (D25).

`add_retention_policy` is TSL-only, so retention is an in-process scheduled job calling
`drop_chunks` (Apache-licensed). Whole chunks are dropped — rows newer than the cutoff are
never touched, so a chunk straddling the cutoff survives until it fully ages out.

`ohlcv_candles` is never pruned: daily history back to 2015 must survive. Ticks are NOT
dropped by any scheduled job from here: app/data/tick_export.py drops a tick chunk only after
its Parquet file is written, read back and matched (slice 1d). This helper stays for those
exports and for tests; nothing calls it on a timer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

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
