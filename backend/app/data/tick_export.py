"""Tick export to Parquet (slice 1d, DEC-1D-TICKS) — cleanup suspended.

Parquet is the permanent record of ticks; the database keeps only a short hot window. For every
chunk older than TICKS_HOT_DAYS:
  1. read it in ONE repeatable-read transaction: aggregates (row count and checksums) and the
     rows themselves come from the same snapshot;
  2. stream the rows into a Parquet file (zstd), written to a temporary name;
  3. read the file back and recompute the same row count and checksums;
  4. only if they are identical: move the file into place and record 'verified'. Any
     difference or error leaves the chunk in the database and flags the failure.
All chunks remain in the database until a concurrency-safe cleanup protocol is implemented.
Verification describes the exported snapshot; subsequent writes may change the database.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from app.core.clock import Clock
from app.core.config import Settings
from app.data.ticks import COLUMNS

log = logging.getLogger(__name__)

_TS = pa.timestamp("us", tz="UTC")
_INT_TYPES: dict[str, pa.DataType] = {
    "sid": pa.int16(), "ltp": pa.int32(), "ltq": pa.int32(), "atp": pa.int32(),
    "volume": pa.int64(), "buy_qty": pa.int64(), "sell_qty": pa.int64(), "oi": pa.int64(),
}  # fmt: skip


def _type(col: str) -> pa.DataType:
    if col in ("ts", "ltt", "ext"):
        return _TS
    if col in _INT_TYPES:
        return _INT_TYPES[col]
    return pa.int16() if col.endswith("o") else pa.int32()  # depth: price, qty, orders


SCHEMA = pa.schema([pa.field(c, _type(c)) for c in COLUMNS])
_SELECT = ", ".join(COLUMNS)
# checksums over columns every tick has; any lost, duplicated or altered row changes them
_AGG_SQL = """SELECT count(*) AS rows, coalesce(sum(sid::bigint), 0) AS sid_sum,
       coalesce(sum(ltp::bigint), 0) AS ltp_sum, coalesce(sum(volume), 0) AS volume_sum,
       coalesce(sum((extract(epoch FROM ts) * 1000000)::bigint), 0) AS ts_sum,
       coalesce(sum(b1q::bigint + a1q::bigint), 0) AS depth_sum
FROM app.ticks WHERE ts >= $1 AND ts < $2"""


class ExportMismatchError(RuntimeError):
    """The Parquet copy differs from the database: the chunk stays, flagged 'mismatch'."""


@dataclass
class ExportReport:
    verified: int = 0
    dropped: int = 0
    failed: int = 0
    rows: int = 0
    details: list[str] = field(default_factory=list)

    def totals(self) -> dict[str, Any]:
        return {
            "chunks_verified": self.verified,
            "chunks_dropped": self.dropped,
            "chunks_failed": self.failed,
            "rows": self.rows,
            "details": self.details[:20],
            "inserted": self.rows,
            "quarantined": self.failed,
        }


def checksums_from_parquet(path: Path) -> dict[str, int]:
    """The same aggregates as _AGG_SQL, recomputed from the file (streamed, batch by batch)."""
    out = {
        "rows": 0,
        "sid_sum": 0,
        "ltp_sum": 0,
        "volume_sum": 0,
        "ts_sum": 0,
        "depth_sum": 0,
        "full_hash": 0,
    }
    for batch in pq.ParquetFile(path).iter_batches(columns=list(COLUMNS)):
        out["rows"] += batch.num_rows
        out["sid_sum"] += pc.sum(batch["sid"].cast(pa.int64())).as_py() or 0
        out["ltp_sum"] += pc.sum(batch["ltp"].cast(pa.int64())).as_py() or 0
        out["volume_sum"] += pc.sum(batch["volume"]).as_py() or 0
        out["ts_sum"] += pc.sum(batch["ts"].cast(pa.int64())).as_py() or 0
        both = pc.add(batch["b1q"].cast(pa.int64()), batch["a1q"].cast(pa.int64()))
        out["depth_sum"] += pc.sum(both).as_py() or 0
        out["full_hash"] = (out["full_hash"] + _full_hash(pa.Table.from_batches([batch]))) % (
            1 << 256
        )
    return out


def _full_hash(table: pa.Table) -> int:
    """Order-independent, duplicate-sensitive digest of every field and null marker."""
    columns = []
    for col in COLUMNS:
        values = table[col]
        if col in ("ts", "ltt", "ext"):
            values = values.cast(pa.int64())
        columns.append(values.to_pylist())
    total = 0
    for row in zip(*columns, strict=True):
        encoded = json.dumps(row, separators=(",", ":")).encode("ascii")
        total += int.from_bytes(hashlib.sha256(encoded).digest(), "big")
    return total % (1 << 256)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _file_for(directory: Path, start: datetime) -> Path:
    return directory / "ticks" / f"date={start:%Y-%m-%d}" / f"ticks_{start:%Y%m%dT%H%M%SZ}.parquet"


async def _write_chunk(
    conn: Any, start: datetime, end: datetime, tmp: Path, batch_rows: int
) -> dict[str, int]:
    """Aggregates + rows from ONE snapshot; returns the database-side checksums."""
    await asyncio.to_thread(tmp.parent.mkdir, parents=True, exist_ok=True)
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        agg = dict(await conn.fetchrow(_AGG_SQL, start, end))
        agg["full_hash"] = 0
        writer = await asyncio.to_thread(pq.ParquetWriter, tmp, SCHEMA, compression="zstd")
        try:
            rows: list[Any] = []
            async for rec in conn.cursor(
                f"SELECT {_SELECT} FROM app.ticks WHERE ts >= $1 AND ts < $2 "  # noqa: S608
                "ORDER BY sid, ts",
                start,
                end,
                prefetch=batch_rows,
            ):
                rows.append(rec)
                if len(rows) >= batch_rows:
                    agg["full_hash"] = (agg["full_hash"] + _full_hash(_table(rows))) % (1 << 256)
                    await asyncio.to_thread(_write, writer, rows)
                    rows = []
            if rows:
                agg["full_hash"] = (agg["full_hash"] + _full_hash(_table(rows))) % (1 << 256)
                await asyncio.to_thread(_write, writer, rows)
        finally:
            await asyncio.to_thread(writer.close)
    return {k: int(v) for k, v in agg.items()}


def _write(writer: Any, rows: list[Any]) -> None:
    writer.write_table(_table(rows))


def _table(rows: list[Any]) -> pa.Table:
    return pa.table(
        {c: pa.array([r[c] for r in rows], type=SCHEMA.field(c).type) for c in COLUMNS},
        schema=SCHEMA,
    )


async def _ledger(
    conn: Any, start: datetime, end: datetime, status: str, now: datetime, **kw: Any
) -> None:
    await conn.execute(
        """INSERT INTO app.tick_exports (chunk_start, chunk_end, status, rows, bytes, sha256, path,
                                         checksums, detail, exported_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
           ON CONFLICT (chunk_start) DO UPDATE SET status = EXCLUDED.status, rows = EXCLUDED.rows,
               bytes = EXCLUDED.bytes, sha256 = EXCLUDED.sha256, path = EXCLUDED.path,
               checksums = EXCLUDED.checksums, detail = EXCLUDED.detail,
               exported_at = EXCLUDED.exported_at""",
        start,
        end,
        status,
        kw.get("rows", 0),
        kw.get("bytes", 0),
        kw.get("sha256"),
        kw.get("path"),
        kw.get("checksums") and __import__("json").dumps(kw["checksums"]),
        kw.get("detail"),
        now,
    )


async def export_ready_chunks(pool: Any, settings: Settings, clock: Clock) -> ExportReport:
    report = ExportReport()
    now = clock.now()
    cutoff = now - timedelta(days=settings.ticks_hot_days)
    directory = settings.ticks_parquet_dir
    async with pool.acquire() as conn:
        chunks = await conn.fetch(
            """SELECT range_start, range_end FROM timescaledb_information.chunks
               WHERE hypertable_schema = 'app' AND hypertable_name = 'ticks'
                 AND range_end <= $1 ORDER BY range_start""",
            cutoff,
        )
    for c in chunks:
        start, end = c["range_start"], c["range_end"]
        try:
            async with pool.acquire() as conn:
                await _one_chunk(conn, start, end, directory, settings, now, report)
        except ExportMismatchError as exc:  # already recorded as 'mismatch' in the ledger
            report.failed += 1
            report.details.append(f"{start:%Y-%m-%d}: {exc}"[:200])
            log.error("tick export of chunk %s: %s", start, exc)
        except Exception as exc:  # one bad chunk never stops the others, and is never dropped
            report.failed += 1
            report.details.append(f"{start:%Y-%m-%d}: {type(exc).__name__}: {exc}"[:200])
            log.exception("tick export of chunk %s failed", start)
            try:
                async with pool.acquire() as conn:
                    await _ledger(conn, start, end, "error", now, detail=str(exc)[:300])
            except Exception:
                log.exception("could not record the failed export")
    return report


async def _one_chunk(
    conn: Any,
    start: datetime,
    end: datetime,
    directory: Path,
    settings: Settings,
    now: datetime,
    report: ExportReport,
) -> None:
    prior = await conn.fetchrow(
        "SELECT status, dropped_at, path, checksums FROM app.tick_exports WHERE chunk_start = $1",
        start,
    )
    if prior and prior["dropped_at"]:
        return  # done long ago
    final = _file_for(directory, start)
    # A fresh snapshot is required even if an earlier export was verified: late
    # writes may have changed the chunk since that verification.
    tmp = final.with_suffix(".parquet.tmp")
    try:
        db_agg = await _write_chunk(conn, start, end, tmp, settings.ticks_export_batch_rows)
        if db_agg["rows"] == 0:
            return
        file_agg = await asyncio.to_thread(checksums_from_parquet, tmp)
        if file_agg != db_agg:
            await _ledger(
                conn, start, end, "mismatch", now, rows=db_agg["rows"],
                checksums={"db": db_agg, "file": file_agg},
                detail="the Parquet file does not match the database — chunk kept",
            )  # fmt: skip
            raise ExportMismatchError("full-record checksum mismatch; chunk kept")
        await asyncio.to_thread(os.replace, tmp, final)
    finally:
        await asyncio.to_thread(tmp.unlink, missing_ok=True)
    size = (await asyncio.to_thread(final.stat)).st_size
    digest = await asyncio.to_thread(_sha256, final)
    await _ledger(
        conn, start, end, "verified", now, rows=db_agg["rows"], bytes=size,
        sha256=digest, path=str(final), checksums=db_agg,
        detail="Snapshot verified; database chunk retained pending concurrency-safe cleanup",
    )  # fmt: skip
    report.verified += 1
    report.rows += db_agg["rows"]
    # Never drop here until verification and deletion are protected from concurrent writers.


def tick_export_job(
    pool: Any, clock: Clock, settings: Settings
) -> Callable[[], Awaitable[dict[str, Any]]]:
    async def run() -> dict[str, Any]:
        report = await export_ready_chunks(pool, settings, clock)
        log.info("tick export: %s", report.totals())
        return report.totals()

    return run
