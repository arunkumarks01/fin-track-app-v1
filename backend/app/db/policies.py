"""Hypertable chunk sizing, driven by config (law 7: nothing hardcoded).

Apache-licensed TimescaleDB only (D25): no compression/columnstore or retention
*policies* (TSL). Retention is an in-process job (app/db/retention.py); disk space is
the Parquet cold tier's job (Phase 1). Applied at every boot; idempotent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.core.config import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkPolicy:
    table: str  # schema-qualified
    chunk_interval: timedelta


def chunk_policies_from(settings: Settings) -> list[ChunkPolicy]:
    return [
        ChunkPolicy("app.ohlcv_candles", timedelta(days=settings.ohlcv_chunk_interval_days)),
        ChunkPolicy("app.ticks", timedelta(hours=settings.ticks_chunk_interval_hours)),
    ]


async def apply_storage_policies(conn: Any, settings: Settings) -> None:
    """Set the chunk interval for future chunks (existing chunks keep theirs)."""
    for p in chunk_policies_from(settings):
        await conn.execute(
            "SELECT set_chunk_time_interval($1::regclass, $2::interval)",
            p.table,
            p.chunk_interval,
        )
        log.info("chunk interval %s = %s", p.table, p.chunk_interval)
