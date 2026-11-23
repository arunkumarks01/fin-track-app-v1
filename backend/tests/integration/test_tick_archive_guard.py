from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from app.db.migrate import migrate

pytestmark = pytest.mark.db


async def test_archived_range_rejects_late_inserts_and_timestamp_updates(db: Any) -> None:
    await migrate(db)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=1)
    await db.execute(
        """INSERT INTO app.tick_exports
           (chunk_start,chunk_end,status,exported_at,dropped_at)
           VALUES ($1,$2,'verified',$2,$2)""",
        start,
        end,
    )
    await db.execute("INSERT INTO app.ticks(ts,sid,ltp) VALUES($1,1,100)", end)
    with pytest.raises(asyncpg.CheckViolationError, match="archived and dropped range"):
        await db.execute("INSERT INTO app.ticks(ts,sid,ltp) VALUES($1,1,100)", start)
    with pytest.raises(asyncpg.CheckViolationError, match="archived and dropped range"):
        await db.execute("UPDATE app.ticks SET ts=$1 WHERE ts=$2", start, end)
    assert await db.fetchval("SELECT count(*) FROM app.ticks") == 1


async def test_verified_but_retained_range_accepts_late_ticks(db: Any) -> None:
    await migrate(db)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=1)
    await db.execute(
        """INSERT INTO app.tick_exports (chunk_start,chunk_end,status,exported_at)
           VALUES ($1,$2,'verified',$2)""",
        start,
        end,
    )
    await db.execute("INSERT INTO app.ticks(ts,sid,ltp) VALUES($1,1,100)", start)
    assert await db.fetchval("SELECT count(*) FROM app.ticks") == 1


async def test_range_guard_observes_marker_after_maintenance_lock(db: Any, test_dsn: str) -> None:
    import asyncio

    await migrate(db)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=1)
    writer = await asyncpg.connect(test_dsn)
    try:
        async with db.transaction():
            await db.execute("LOCK TABLE app.ticks IN SHARE ROW EXCLUSIVE MODE")
            await db.execute(
                """INSERT INTO app.tick_exports
                   (chunk_start,chunk_end,status,exported_at,dropped_at)
                   VALUES ($1,$2,'verified',$2,$2)""",
                start,
                end,
            )
            attempt = asyncio.create_task(
                writer.execute("INSERT INTO app.ticks(ts,sid,ltp) VALUES($1,1,100)", start)
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(attempt), timeout=0.2)
        with pytest.raises(asyncpg.CheckViolationError, match="archived and dropped range"):
            await attempt
        assert await db.fetchval("SELECT count(*) FROM app.ticks") == 0
    finally:
        await writer.close()


async def test_maintenance_waits_for_an_existing_tick_writer(db: Any, test_dsn: str) -> None:
    import asyncio

    await migrate(db)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    writer = await asyncpg.connect(test_dsn)
    transaction = writer.transaction()
    await transaction.start()
    try:
        await writer.execute("INSERT INTO app.ticks(ts,sid,ltp) VALUES($1,1,100)", start)
        async with db.transaction():
            lock = asyncio.create_task(
                db.execute("LOCK TABLE app.ticks IN SHARE ROW EXCLUSIVE MODE")
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(lock), timeout=0.2)
            await transaction.commit()
            await lock
            assert await db.fetchval("SELECT count(*) FROM app.ticks") == 1
    finally:
        await writer.close()
