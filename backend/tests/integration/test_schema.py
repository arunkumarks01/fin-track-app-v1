"""Migrations, hypertables, chunking, retention and the trading-date dedup key (real DB)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.db.migrate import Migration, MigrationError, discover, migrate
from app.db.policies import apply_storage_policies
from app.db.retention import TICKS_TABLE, drop_expired_chunks, ticks_cleanup_job
from tests.conftest import build_settings
from tests.fixtures.candles import Candle, Pattern, make_daily_candles
from tests.fixtures.clock import FrozenClock

pytestmark = pytest.mark.db

INSERT = """
INSERT INTO app.ohlcv_candles (symbol, timeframe, ts, open, high, low, close, volume)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""
INSERT_DEDUP = INSERT + " ON CONFLICT (symbol, timeframe, ts) DO NOTHING"


def _row(c: Candle) -> tuple[Any, ...]:
    return (c.symbol, c.timeframe, c.ts, c.open, c.high, c.low, c.close, c.volume)


# --- migrations runner ---
async def test_migrate_is_ordered_and_idempotent(db: Any) -> None:
    applied = await migrate(db)
    assert applied == [m.version for m in discover()]
    assert await migrate(db) == []
    rows = await db.fetch("SELECT version, checksum FROM app.applied_migrations ORDER BY version")
    assert [(r["version"], r["checksum"]) for r in rows] == [
        (m.version, m.checksum) for m in discover()
    ]


async def test_modified_applied_migration_aborts(db: Any) -> None:
    await migrate(db)
    tampered = [Migration(m.version, m.name, m.sql + "\n-- edited") for m in discover()]
    with pytest.raises(MigrationError, match="modified after being applied"):
        await migrate(db, tampered)


async def test_db_ahead_of_code_aborts(db: Any) -> None:
    await migrate(db)
    await db.execute(
        "INSERT INTO app.applied_migrations (version, name, checksum) VALUES (99, 'future', 'x')"
    )
    with pytest.raises(MigrationError, match="unknown to this code"):
        await migrate(db)


async def test_failed_migration_rolls_back(db: Any) -> None:
    broken = [*discover(), Migration(len(discover()) + 1, "broken", "CREATE TABLE app.x(); SELEC")]
    with pytest.raises(asyncpg.PostgresSyntaxError):
        await migrate(db, broken)
    assert await db.fetchval("SELECT max(version) FROM app.applied_migrations") == len(discover())
    assert await db.fetchval("SELECT to_regclass('app.x')") is None


# --- hypertables + chunking (Apache build: no TSL features) ---
async def test_hypertables_exist_without_tsl_features(db: Any) -> None:
    await migrate(db)
    rows = await db.fetch(
        """SELECT hypertable_name, compression_enabled
           FROM timescaledb_information.hypertables WHERE hypertable_schema = 'app'
           ORDER BY hypertable_name"""
    )
    assert [(r["hypertable_name"], r["compression_enabled"]) for r in rows] == [
        ("ohlcv_1m", False),
        ("ohlcv_candles", False),
        ("ticks", False),
    ]
    jobs = await db.fetchval(
        "SELECT count(*) FROM timescaledb_information.jobs WHERE hypertable_schema = 'app'"
    )
    assert jobs == 0  # no TSL policy jobs; retention is our own scheduled job


async def test_chunk_intervals_follow_config(db: Any, db_settings: Settings) -> None:
    await migrate(db)
    changed = build_settings(
        database_url=db_settings.database_url.get_secret_value(),
        ohlcv_chunk_interval_days=7,
        ticks_chunk_interval_hours=6,
    )
    await apply_storage_policies(db, changed)
    await apply_storage_policies(db, changed)  # idempotent
    rows = await db.fetch(
        """SELECT hypertable_name, time_interval FROM timescaledb_information.dimensions
           WHERE hypertable_schema = 'app' ORDER BY hypertable_name"""
    )
    assert [(r["hypertable_name"], r["time_interval"]) for r in rows] == [
        ("ohlcv_1m", timedelta(days=7)),
        ("ohlcv_candles", timedelta(days=7)),
        ("ticks", timedelta(hours=6)),
    ]


# --- retention fallback: drop_chunks job ---
TICK_INSERT = "INSERT INTO app.ticks (symbol, ts, last_price) VALUES ($1, $2, $3)"
NOW = datetime(2024, 6, 1, 3, 0, tzinfo=UTC)


async def _seed_ticks(db: Any, settings: Settings) -> None:
    await migrate(db)
    await apply_storage_policies(db, settings)  # 24h chunks
    ages = [timedelta(days=d) for d in (120, 100, 91, 89, 1)]
    await db.executemany(TICK_INSERT, [("INFY", NOW - a, 150_000) for a in ages])


async def test_retention_drops_only_fully_expired_chunks(db: Any, db_settings: Settings) -> None:
    await _seed_ticks(db, db_settings)
    dropped = await drop_expired_chunks(db, TICKS_TABLE, NOW - timedelta(days=90))
    assert len(dropped) == 3
    remaining = await db.fetch("SELECT ts FROM app.ticks ORDER BY ts")
    assert [NOW - r["ts"] for r in remaining] == [timedelta(days=89), timedelta(days=1)]
    assert await drop_expired_chunks(db, TICKS_TABLE, NOW - timedelta(days=90)) == []


async def test_retention_never_touches_candles(db: Any, db_settings: Settings) -> None:
    await _seed_ticks(db, db_settings)
    old = make_daily_candles(Pattern.TREND, n=5, start=date(2015, 1, 1))
    await db.executemany(INSERT, [_row(c) for c in old])
    await drop_expired_chunks(db, TICKS_TABLE, NOW - timedelta(days=90))
    assert await db.fetchval("SELECT count(*) FROM app.ohlcv_candles") == 5


async def test_cleanup_job_uses_clock_and_config(test_dsn: str, db: Any) -> None:
    settings = build_settings(database_url=test_dsn, ticks_retention_days=95)
    await _seed_ticks(db, settings)
    pool = await asyncpg.create_pool(test_dsn, min_size=1, max_size=1)
    try:
        job = ticks_cleanup_job(pool, FrozenClock(NOW), settings)
        assert len(await job()) == 2  # 120d and 100d chunks; 91d is within 95d
    finally:
        await pool.close()
    assert await db.fetchval("SELECT count(*) FROM app.ticks") == 3


# --- dedup key = trading date ---
async def test_same_trading_day_lands_once(db: Any) -> None:
    await migrate(db)
    candles = make_daily_candles(Pattern.TREND, n=20)
    await db.executemany(INSERT, [_row(c) for c in candles])
    await db.executemany(INSERT_DEDUP, [_row(c) for c in candles])  # re-ingest
    assert await db.fetchval("SELECT count(*) FROM app.ohlcv_candles") == 20
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(INSERT, *_row(candles[0]))


async def test_non_canonical_daily_ts_rejected(db: Any) -> None:
    await migrate(db)
    c = make_daily_candles(Pattern.TREND, n=2)[0]
    off_midnight = datetime.combine(date(2024, 1, 1), datetime.min.time(), MARKET_TZ).replace(
        hour=9, minute=15
    )
    with pytest.raises(asyncpg.CheckViolationError, match="ohlcv_daily_canonical_ts"):
        await db.execute(INSERT, c.symbol, "1d", off_midnight, *_row(c)[3:])
    # UTC midnight is also not the IST trading-date identity
    utc_midnight = canonical_daily_ts(date(2024, 1, 1), MARKET_TZ) + timedelta(hours=5, minutes=30)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(INSERT, c.symbol, "1d", utc_midnight, *_row(c)[3:])
    # intraday timeframes keep their exact ts
    await db.execute(INSERT, c.symbol, "5m", off_midnight, *_row(c)[3:])


async def test_bad_bar_rejected_by_db_backstop(db: Any) -> None:
    await migrate(db)
    bad = next(c for c in make_daily_candles(Pattern.BAD_BAR, n=10) if not c.is_well_formed())
    with pytest.raises(asyncpg.CheckViolationError, match="ohlcv_ohlc_order"):
        await db.execute(INSERT, *_row(bad))
    assert await db.fetchval("SELECT count(*) FROM app.ohlcv_candles") == 0


async def test_split_bar_is_kept(db: Any) -> None:
    """A large gap (suspected corporate action) is flag-but-keep, never dropped."""
    await migrate(db)
    candles = make_daily_candles(Pattern.SPLIT, n=10)
    await db.executemany(INSERT, [_row(c) for c in candles])
    assert await db.fetchval("SELECT count(*) FROM app.ohlcv_candles") == 10
