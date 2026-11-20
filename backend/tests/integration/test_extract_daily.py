"""Daily extractor on the real DB: resumable, idempotent, honest about partial/late bars."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, time
from typing import Any

import asyncpg
import pytest

from app.broker.kite.client import KiteAuthError, KiteUnavailableError
from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.data.extract_daily import run_daily_backfill
from app.data.instruments import UniverseMember, sync_universe
from app.db.migrate import migrate
from tests.conftest import build_settings
from tests.fixtures.candles import Pattern, make_daily_candles
from tests.fixtures.clock import FrozenClock
from tests.fixtures.historical import FakeHistorical, kite_record

pytestmark = pytest.mark.db

IST = MARKET_TZ
START = date(2024, 1, 1)
# 2024-03-15 is a Friday; its 1d bar is final from Sat 08:15 IST (Kite publishes the official
# close overnight), so "now" is the next morning
EVENING = datetime(2024, 3, 16, 9, 0, tzinfo=IST)
MEMBERS = [UniverseMember("NSE:AAA", 101, "equity"), UniverseMember("NSE:BBB", 202, "equity")]


def settings_for(test_dsn: str, **kw: Any) -> Settings:
    base: dict[str, Any] = {"ingest_daily_start": START, "kite_window_days": {"day": 30}}
    base.update(kw)
    return build_settings(database_url=test_dsn, **base)


def series(pattern: Pattern = Pattern.TREND, n: int = 55) -> list[list[Any]]:
    # 55 weekdays from 2024-01-01 -> last bar 2024-03-15
    return [kite_record(c) for c in make_daily_candles(pattern, n=n, start=START)]


@pytest.fixture
async def pool(test_dsn: str, db: Any) -> AsyncIterator[Any]:
    await migrate(db)
    await sync_universe(db, MEMBERS, EVENING)
    p = await asyncpg.create_pool(test_dsn, min_size=1, max_size=2)
    try:
        yield p
    finally:
        await p.close()


async def _count(db: Any, symbol: str) -> int:
    return int(
        await db.fetchval(
            "SELECT count(*) FROM app.ohlcv_candles WHERE symbol = $1 AND timeframe = '1d'", symbol
        )
    )


async def _cursor(db: Any, symbol: str) -> Any:
    return await db.fetchrow(
        "SELECT last_ts, status, last_error, rows_written FROM app.ingest_cursor "
        "WHERE symbol = $1 AND timeframe = '1d'",
        symbol,
    )


async def test_backfill_then_idempotent_rerun(pool: Any, db: Any, test_dsn: str) -> None:
    src = FakeHistorical({101: series(), 202: series(Pattern.CHOP)})
    s, clock = settings_for(test_dsn), FrozenClock(EVENING)

    report = await run_daily_backfill(pool, src, s, clock)
    assert report.stopped_reason is None
    assert report.totals()["inserted"] == 110
    assert await _count(db, "NSE:AAA") == 55
    cur = await _cursor(db, "NSE:AAA")
    assert (cur["last_ts"], cur["status"], cur["rows_written"]) == (
        canonical_daily_ts(date(2024, 3, 15), IST),
        "ok",
        55,
    )
    # 2024-01-01..2024-03-15 = 75 days -> 3 windows of <=30 days per symbol
    assert len(src.calls) == 6
    assert all(c[2].time() == time(0) and c[3].time() == time(23, 59, 59) for c in src.calls)

    again = await run_daily_backfill(pool, src, s, clock)
    # nothing new — but the newest stored session is re-fetched once per symbol: that is the
    # drift re-check (Kite may re-adjust history), and it is recorded as checked
    assert [(r.status, r.inserted, r.already_stored) for r in again.results] == [
        ("ok", 0, 1),
        ("ok", 0, 1),
    ]
    assert len(src.calls) == 8
    assert {c[2].date() for c in src.calls[6:]} == {date(2024, 3, 15)}
    checks = await db.fetch("SELECT symbol, checked_on FROM app.adjustment_checks ORDER BY 1")
    assert [(r["symbol"], r["checked_on"]) for r in checks] == [
        ("NSE:AAA", date(2024, 3, 15)),
        ("NSE:BBB", date(2024, 3, 15)),
    ]
    dupes = await db.fetchval(
        "SELECT count(*) - count(DISTINCT (symbol, ts)) FROM app.ohlcv_candles"
    )
    assert dupes == 0


async def test_partial_bar_never_fetched_before_final_time(
    pool: Any, db: Any, test_dsn: str
) -> None:
    src = FakeHistorical({101: series()})
    morning = FrozenClock(datetime(2024, 3, 15, 11, 0, tzinfo=IST))  # market open
    await run_daily_backfill(pool, src, settings_for(test_dsn), morning, symbols=["NSE:AAA"])
    assert src.calls[-1][3].date() == date(2024, 3, 14)
    assert await _count(db, "NSE:AAA") == 54
    # regression (1a bug, 2026-11-19): the same evening the 1d bar is still provisional
    evening = FrozenClock(datetime(2024, 3, 15, 18, 25, tzinfo=IST))
    await run_daily_backfill(pool, src, settings_for(test_dsn), evening, symbols=["NSE:AAA"])
    assert await _count(db, "NSE:AAA") == 54


async def test_crash_mid_run_resumes_without_duplicates(pool: Any, db: Any, test_dsn: str) -> None:
    s, clock = settings_for(test_dsn), FrozenClock(EVENING)
    src = FakeHistorical({101: series()}, fail_on_call={1: KiteUnavailableError("503s")})
    first = await run_daily_backfill(pool, src, s, clock, symbols=["NSE:AAA"])
    assert first.results[0].status == "error"
    cur = await _cursor(db, "NSE:AAA")
    assert cur["status"] == "error"
    assert "503s" in cur["last_error"]
    assert cur["last_ts"] == canonical_daily_ts(date(2024, 1, 30), IST)  # end of window 1
    partial = await _count(db, "NSE:AAA")
    assert 0 < partial < 55

    src.fail_on_call.clear()
    second = await run_daily_backfill(pool, src, s, clock, symbols=["NSE:AAA"])
    assert second.results[0].status == "ok"
    assert await _count(db, "NSE:AAA") == 55
    assert (await _cursor(db, "NSE:AAA"))["rows_written"] == 55


async def test_auth_error_stops_whole_run(pool: Any, db: Any, test_dsn: str) -> None:
    src = FakeHistorical({101: series(), 202: series()}, fail_on_call={0: KiteAuthError("expired")})
    report = await run_daily_backfill(pool, src, settings_for(test_dsn), FrozenClock(EVENING))
    assert report.stopped_reason is not None
    assert "expired" in report.stopped_reason
    assert [r.status for r in report.results] == ["auth_stopped"]  # BBB never attempted
    assert len(src.calls) == 1
    assert await _count(db, "NSE:AAA") == 0


async def test_bad_bar_quarantined_split_flagged(pool: Any, db: Any, test_dsn: str) -> None:
    src = FakeHistorical({101: series(Pattern.BAD_BAR), 202: series(Pattern.SPLIT)})
    await run_daily_backfill(pool, src, settings_for(test_dsn), FrozenClock(EVENING))
    q = await db.fetch("SELECT symbol, reason, raw FROM app.ohlcv_quarantine")
    assert [(r["symbol"], r["reason"]) for r in q] == [("NSE:AAA", "ohlc_order")]
    assert q[0]["raw"]  # raw payload kept
    assert await _count(db, "NSE:AAA") == 54
    assert await _count(db, "NSE:BBB") == 55  # split bar kept
    flags = await db.fetch("SELECT symbol, flag FROM app.ohlcv_flags")
    assert [(f["symbol"], f["flag"]) for f in flags] == [("NSE:BBB", "large_gap")]


async def test_revised_bar_quarantined_stored_kept(pool: Any, db: Any, test_dsn: str) -> None:
    s, clock = settings_for(test_dsn), FrozenClock(EVENING)
    original = series()
    await run_daily_backfill(pool, FakeHistorical({101: original}), s, clock, symbols=["NSE:AAA"])
    stored = await db.fetchval(
        "SELECT close FROM app.ohlcv_candles WHERE symbol='NSE:AAA' ORDER BY ts LIMIT 1"
    )

    revised = [list(r) for r in original]
    revised[0][4] = revised[0][4] / 2  # e.g. a later split re-adjustment by the vendor
    revised[0][3] = min(revised[0][3], revised[0][4])
    await db.execute("DELETE FROM app.ingest_cursor")  # force a full re-fetch
    await run_daily_backfill(pool, FakeHistorical({101: revised}), s, clock, symbols=["NSE:AAA"])

    assert (
        await db.fetchval(
            "SELECT close FROM app.ohlcv_candles WHERE symbol='NSE:AAA' ORDER BY ts LIMIT 1"
        )
        == stored
    )
    reasons = [r["reason"] for r in await db.fetch("SELECT reason FROM app.ohlcv_quarantine")]
    assert reasons == ["conflicts_with_stored"]
    assert await _count(db, "NSE:AAA") == 55


async def test_late_published_bar_is_fetched_next_run(pool: Any, db: Any, test_dsn: str) -> None:
    s, clock = settings_for(test_dsn), FrozenClock(EVENING)
    full = series()
    src = FakeHistorical({101: full[:-1]})  # Kite hasn't published 2024-03-15 yet
    await run_daily_backfill(pool, src, s, clock, symbols=["NSE:AAA"])
    assert (await _cursor(db, "NSE:AAA"))["last_ts"] == canonical_daily_ts(date(2024, 3, 14), IST)

    src.series[101] = full  # now it is
    await run_daily_backfill(pool, src, s, clock, symbols=["NSE:AAA"])
    assert await _count(db, "NSE:AAA") == 55
    # resumes ON the newest stored session (the drift re-check), which brings 03-15 with it
    assert src.calls[-1][2].date() == date(2024, 3, 14)


async def test_empty_universe_fails_closed(test_dsn: str, db: Any) -> None:
    await migrate(db)
    p = await asyncpg.create_pool(test_dsn, min_size=1, max_size=1)
    try:
        report = await run_daily_backfill(
            p, FakeHistorical(), settings_for(test_dsn), FrozenClock(EVENING)
        )
    finally:
        await p.close()
    assert report.stopped_reason is not None
    assert "universe is empty" in report.stopped_reason


def legacy_stamp(record: list[Any]) -> list[Any]:
    """Kite's pre-Dec-2018 daily format: same trading date, stamped 09:15 IST."""
    return [str(record[0]).replace("T00:00:00", "T09:15:00"), *record[1:]]


async def test_legacy_stamped_history_stored_and_flagged(pool: Any, db: Any, test_dsn: str) -> None:
    rows = series()
    src = FakeHistorical({101: [legacy_stamp(r) for r in rows[:20]] + rows[20:]})
    report = await run_daily_backfill(
        pool, src, settings_for(test_dsn), FrozenClock(EVENING), symbols=["NSE:AAA"]
    )
    assert report.results[0].inserted == 55
    assert await db.fetchval("SELECT count(*) FROM app.ohlcv_quarantine") == 0
    flags = await db.fetch("SELECT flag, detail FROM app.ohlcv_flags")
    assert {f["flag"] for f in flags} == {"retimed_ts"}
    assert len(flags) == 20
    times = await db.fetch(
        "SELECT DISTINCT (ts AT TIME ZONE 'Asia/Kolkata')::time AS t FROM app.ohlcv_candles"
    )
    assert [r["t"] for r in times] == [time(0)]  # every stored daily bar is canonical


async def test_targeted_reset_refetches_without_duplicates(
    pool: Any, db: Any, test_dsn: str
) -> None:
    s, clock = settings_for(test_dsn), FrozenClock(EVENING)
    src = FakeHistorical({101: series(), 202: series()})
    await run_daily_backfill(pool, src, s, clock)
    calls = len(src.calls)

    again = await run_daily_backfill(pool, src, s, clock, symbols=["NSE:AAA"], reset=True)
    assert again.results[0].status == "ok"
    assert again.results[0].already_stored == 55
    assert again.results[0].inserted == 0
    assert len(src.calls) == calls + 3  # only AAA's 3 windows re-fetched
    assert await _count(db, "NSE:AAA") == 55

    with pytest.raises(ValueError, match="explicit symbol list"):
        await run_daily_backfill(pool, src, s, clock, reset=True)
    with pytest.raises(ValueError, match="NSE:NOPE"):
        await run_daily_backfill(pool, src, s, clock, symbols=["NSE:NOPE"], reset=True)


async def test_requarantining_same_bad_row_is_deduplicated(
    pool: Any, db: Any, test_dsn: str
) -> None:
    s, clock = settings_for(test_dsn), FrozenClock(EVENING)
    src = FakeHistorical({101: series(Pattern.BAD_BAR)})
    await run_daily_backfill(pool, src, s, clock, symbols=["NSE:AAA"])
    await run_daily_backfill(pool, src, s, clock, symbols=["NSE:AAA"], reset=True)
    assert await db.fetchval("SELECT count(*) FROM app.ohlcv_quarantine") == 1
