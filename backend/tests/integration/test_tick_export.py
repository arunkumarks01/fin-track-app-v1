"""Slice 1d: ticks leave the database only after a verified Parquet copy exists."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import pyarrow.parquet as pq
import pytest

from app.data import tick_export
from app.data.tick_export import export_ready_chunks
from app.db.migrate import migrate
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 23, 4, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=20)
RECENT = NOW - timedelta(days=1)


@pytest.fixture
async def pool(test_dsn: str, db: Any) -> AsyncIterator[Any]:
    await migrate(db)
    p = await asyncpg.create_pool(test_dsn, min_size=1, max_size=3)
    try:
        yield p
    finally:
        await p.close()


def _size(path: Path) -> int:
    return path.stat().st_size


def _blocker(tmp_path: Path) -> Path:
    path = tmp_path / "file-not-dir"
    path.write_text("x")
    return path


def _files(directory: Path, pattern: str) -> list[Path]:
    return list(directory.rglob(pattern))


async def _seed(db: Any) -> None:
    rows: list[tuple[Any, ...]] = []
    for i in range(250):
        ts = OLD.replace(hour=4, minute=0, second=0) + timedelta(seconds=i, microseconds=i)
        rows.append(
            (
                *(ts, 1 + i % 3, 100_000 + i, 5, 90_000, 1000 + i, 10, 20, 0),
                *(99_990 + i, 7 + i % 4, 2, 100_010 + i, 9, 1),  # best bid and ask
            )
        )
    rows.append(
        (RECENT, 1, 123_456, None, None, None, None, None, None, None, None, None, None, None, None)
    )
    await db.executemany(
        """INSERT INTO app.ticks (ts, sid, ltp, ltq, atp, volume, buy_qty, sell_qty, oi,
                                  b1p, b1q, b1o, a1p, a1q, a1o) VALUES
           ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)""",
        rows,
    )


def _settings(dsn: str, directory: Path, **kw: Any) -> Any:
    return build_settings(
        database_url=dsn,
        ticks_parquet_dir=directory,
        ticks_hot_days=7,
        ticks_export_batch_rows=1000,
        **kw,
    )


async def test_an_old_chunk_is_exported_verified_and_retained(
    pool: Any, db: Any, test_dsn: str, tmp_path: Path
) -> None:
    await _seed(db)
    before = await db.fetch(
        "SELECT sid, ts, ltp, volume, b1q, a1q FROM app.ticks WHERE ts < $1 ORDER BY sid, ts",
        NOW - timedelta(days=7),
    )
    r = await export_ready_chunks(pool, _settings(test_dsn, tmp_path), FrozenClock(NOW))
    assert (r.verified, r.dropped, r.failed, r.rows) == (1, 0, 0, 250)

    files = _files(tmp_path, "*.parquet")
    assert len(files) == 1
    assert not _files(tmp_path, "*.tmp")
    t = pq.read_table(files[0])
    assert t.num_rows == 250
    assert t.column("ltp").to_pylist() == [r["ltp"] for r in before]  # same rows, sorted (sid, ts)
    assert t.schema.field("ts").type.tz == "UTC"
    assert files[0].parent.name == f"date={OLD:%Y-%m-%d}"

    assert await db.fetchval("SELECT count(*) FROM app.ticks") == 251  # cleanup remains suspended
    led = await db.fetchrow("SELECT status, rows, dropped_at, sha256, bytes FROM app.tick_exports")
    assert (led["status"], led["rows"]) == ("verified", 250)
    assert led["dropped_at"] is None
    assert len(led["sha256"]) == 64
    assert led["bytes"] == _size(files[0])

    again = await export_ready_chunks(pool, _settings(test_dsn, tmp_path), FrozenClock(NOW))
    assert (again.verified, again.dropped, again.failed) == (1, 0, 0)  # re-check fresh snapshot


async def test_a_file_that_does_not_match_keeps_the_chunk_and_leaves_no_file(
    pool: Any, db: Any, test_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db)
    real = tick_export.checksums_from_parquet

    def altered(path: Path) -> dict[str, int]:
        out = real(path)
        out["ltp_sum"] += 1  # one price differs
        return out

    monkeypatch.setattr(tick_export, "checksums_from_parquet", altered)
    r = await export_ready_chunks(pool, _settings(test_dsn, tmp_path), FrozenClock(NOW))
    assert (r.verified, r.dropped, r.failed) == (0, 0, 1)
    assert await db.fetchval("SELECT count(*) FROM app.ticks") == 251  # nothing was dropped
    assert not _files(tmp_path, "*.parquet*")  # neither the file nor its temp copy remains
    assert await db.fetchval("SELECT status FROM app.tick_exports") == "mismatch"


async def test_an_unwritable_directory_is_an_error_not_a_drop(
    pool: Any, db: Any, test_dsn: str, tmp_path: Path
) -> None:
    await _seed(db)
    blocker = _blocker(tmp_path)
    r = await export_ready_chunks(pool, _settings(test_dsn, blocker), FrozenClock(NOW))
    assert (r.dropped, r.failed) == (0, 1)
    assert await db.fetchval("SELECT count(*) FROM app.ticks") == 251
    assert await db.fetchval("SELECT status FROM app.tick_exports") == "error"


async def test_promotion_failure_keeps_chunk_and_does_not_verify(
    pool: Any, db: Any, test_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db)

    def fail_promotion(tmp: Path, final: Path) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(tick_export, "_promote", fail_promotion)
    result = await export_ready_chunks(pool, _settings(test_dsn, tmp_path), FrozenClock(NOW))
    assert (result.verified, result.dropped, result.failed) == (0, 0, 1)
    assert await db.fetchval("SELECT count(*) FROM app.ticks") == 251
    assert await db.fetchval("SELECT status FROM app.tick_exports") == "error"
    assert not _files(tmp_path, "*.tmp")


async def test_second_export_does_not_overwrite_running_export(
    pool: Any, db: Any, test_dsn: str, tmp_path: Path
) -> None:
    await _seed(db)
    start = await db.fetchval(
        """SELECT range_start FROM timescaledb_information.chunks
           WHERE hypertable_schema='app' AND hypertable_name='ticks'
             AND range_end <= $1 ORDER BY range_start LIMIT 1""",
        NOW - timedelta(days=7),
    )
    key = f"fintrack:tick-export:{start.isoformat()}"
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", key)
        try:
            result = await export_ready_chunks(
                pool, _settings(test_dsn, tmp_path), FrozenClock(NOW)
            )
            assert result.verified == 0
            assert any("already running" in detail for detail in result.details)
            assert await db.fetchval("SELECT count(*) FROM app.ticks") == 251
            assert not _files(tmp_path, "*.parquet")
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", key)


async def test_previously_dropped_range_is_not_silently_overwritten(
    pool: Any, db: Any, test_dsn: str, tmp_path: Path
) -> None:
    await _seed(db)
    first = await export_ready_chunks(pool, _settings(test_dsn, tmp_path), FrozenClock(NOW))
    assert first.verified == 1
    archive = _files(tmp_path, "*.parquet")[0]
    original = archive.read_bytes()
    await db.execute("UPDATE app.tick_exports SET dropped_at=$1", NOW)
    again = await export_ready_chunks(pool, _settings(test_dsn, tmp_path), FrozenClock(NOW))
    assert (again.verified, again.dropped, again.failed) == (0, 0, 1)
    assert "manual review required" in again.details[0]
    assert archive.read_bytes() == original
    assert await db.fetchval("SELECT count(*) FROM app.ticks") == 251


async def test_experimental_retirement_verifies_and_rejects_later_writes(
    pool: Any, db: Any, test_dsn: str, tmp_path: Path
) -> None:
    await _seed(db)
    result = await export_ready_chunks(pool, _settings(test_dsn, tmp_path), FrozenClock(NOW))
    assert result.verified == 1
    record = await db.fetchrow("SELECT chunk_start,chunk_end FROM app.tick_exports")
    await tick_export._retire_verified_chunk(db, *record, NOW, 1000)
    assert await db.fetchval("SELECT count(*) FROM app.ticks") == 1
    assert await db.fetchval("SELECT dropped_at FROM app.tick_exports") == NOW
    with pytest.raises(asyncpg.CheckViolationError, match="archived and dropped range"):
        await db.execute("INSERT INTO app.ticks(ts,sid,ltp) VALUES($1,1,1)", OLD)


async def test_experimental_retirement_keeps_chunk_if_late_tick_changes_snapshot(
    pool: Any, db: Any, test_dsn: str, tmp_path: Path
) -> None:
    await _seed(db)
    await export_ready_chunks(pool, _settings(test_dsn, tmp_path), FrozenClock(NOW))
    record = await db.fetchrow("SELECT chunk_start,chunk_end FROM app.tick_exports")
    await db.execute("INSERT INTO app.ticks(ts,sid,ltp) VALUES($1,1,1)", OLD)
    with pytest.raises(tick_export.ExportMismatchError, match="locked database snapshot"):
        await tick_export._retire_verified_chunk(db, *record, NOW, 1000)
    assert await db.fetchval("SELECT count(*) FROM app.ticks") == 252
    assert await db.fetchval("SELECT dropped_at FROM app.tick_exports") is None


async def test_experimental_retirement_rolls_back_marker_on_drop_failure(
    pool: Any, db: Any, test_dsn: str, tmp_path: Path
) -> None:
    await _seed(db)
    await export_ready_chunks(pool, _settings(test_dsn, tmp_path), FrozenClock(NOW))
    record = await db.fetchrow("SELECT chunk_start,chunk_end FROM app.tick_exports")

    class FailDrop:
        def __getattr__(self, name: str) -> Any:
            return getattr(db, name)

        async def fetch(self, query: str, *args: Any) -> Any:
            if "drop_chunks" in query:
                raise RuntimeError("simulated drop failure")
            return await db.fetch(query, *args)

    with pytest.raises(RuntimeError, match="simulated drop failure"):
        await tick_export._retire_verified_chunk(FailDrop(), *record, NOW, 1000)
    assert await db.fetchval("SELECT count(*) FROM app.ticks") == 251
    assert await db.fetchval("SELECT dropped_at FROM app.tick_exports") is None


async def test_experimental_retirement_keeps_chunk_when_archive_changes(
    pool: Any, db: Any, test_dsn: str, tmp_path: Path
) -> None:
    await _seed(db)
    await export_ready_chunks(pool, _settings(test_dsn, tmp_path), FrozenClock(NOW))
    record = await db.fetchrow("SELECT chunk_start,chunk_end,path FROM app.tick_exports")
    await asyncio.to_thread(Path(record["path"]).write_bytes, b"corrupted")
    with pytest.raises(tick_export.ExportMismatchError, match="archive file checksum changed"):
        await tick_export._retire_verified_chunk(
            db, record["chunk_start"], record["chunk_end"], NOW, 1000
        )
    assert await db.fetchval("SELECT count(*) FROM app.ticks") == 251
    assert await db.fetchval("SELECT dropped_at FROM app.tick_exports") is None


async def test_retirement_blocks_concurrent_late_writer_until_marker_commits(
    pool: Any, db: Any, test_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db)
    await export_ready_chunks(pool, _settings(test_dsn, tmp_path), FrozenClock(NOW))
    record = await db.fetchrow("SELECT chunk_start,chunk_end FROM app.tick_exports")
    locked = asyncio.Event()
    release = asyncio.Event()
    real = tick_export._locked_checksums

    async def hold_lock(*args: Any) -> dict[str, int]:
        locked.set()
        await release.wait()
        return await real(*args)

    monkeypatch.setattr(tick_export, "_locked_checksums", hold_lock)
    writer = await asyncpg.connect(test_dsn)
    retirement = asyncio.create_task(tick_export._retire_verified_chunk(db, *record, NOW, 1000))
    try:
        await asyncio.wait_for(locked.wait(), timeout=5)
        late = asyncio.create_task(
            writer.execute("INSERT INTO app.ticks(ts,sid,ltp) VALUES($1,1,1)", OLD)
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(late), timeout=0.2)
        release.set()
        await retirement
        with pytest.raises(asyncpg.CheckViolationError, match="archived and dropped range"):
            await late
        assert await db.fetchval("SELECT count(*) FROM app.ticks") == 1
    finally:
        release.set()
        if not retirement.done():
            await retirement
        await writer.close()
