"""Golden tests for THE intraday derivation + calendar-aware completeness, on real Kite data.

Fixture: real Kite 1m bars and Kite's own native 5m/15m/30m/60m bars for NIFTY 50 and
RELIANCE on 2026-11-18 (RELIANCE is genuinely missing 15:15-15:29) and on the 2024-11-01
muhurat session (18:00-18:59). The derivation must reproduce Kite's native bars EXACTLY.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from app.core.money import rupees_to_paisa
from app.core.tradingdate import MARKET_TZ
from app.data.extract_intraday import refresh_exchange_sessions, run_intraday_backfill
from app.data.instruments import UniverseMember, sync_universe
from app.data.intraday import derive_bars
from app.data.intraday_coverage import completeness, review_sessions
from app.db.migrate import migrate
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock
from tests.fixtures.historical import FakeHistorical
from tests.fixtures.minutes import golden

pytestmark = pytest.mark.db

IST = MARKET_TZ
NORMAL, MUHURAT = date(2026, 11, 18), date(2024, 11, 1)
KITE_TF = {"5m": "5minute", "15m": "15minute", "30m": "30minute", "1h": "60minute"}
UNIVERSE = [
    UniverseMember("NSE:NIFTY 50", 256265, "index"),
    UniverseMember("NSE:RELIANCE", 738561, "equity"),
]


def _dec(row: list[Any]) -> list[Any]:
    return [row[0], *(Decimal(x) if isinstance(x, str) else x for x in row[1:])]


@pytest.fixture
async def loaded(test_dsn: str, db: Any) -> AsyncIterator[Any]:
    """Ingest the golden 1m bars through the real extractor path."""
    await migrate(db)
    await sync_universe(db, UNIVERSE, datetime(2026, 11, 18, 18, 0, tzinfo=IST))
    series: dict[int, list[list[Any]]] = {}
    for case in golden()["cases"]:
        series.setdefault(case["instrument_token"], []).extend(
            _dec(r) for r in case["bars"]["minute"]
        )
    settings = build_settings(
        database_url=test_dsn,
        intraday_floor_date=date(2024, 10, 1),
        kite_window_days={"minute": 1000},
    )
    pool = await asyncpg.create_pool(test_dsn, min_size=1, max_size=2)
    try:
        report = await run_intraday_backfill(
            pool,
            FakeHistorical(series),
            settings,
            FrozenClock(datetime(2026, 11, 18, 18, 0, tzinfo=IST)),
        )
        assert report.stopped_reason is None
        yield db
    finally:
        await pool.close()


def _native(symbol: str, day: date, kite_tf: str) -> list[tuple[Any, ...]]:
    case = next(
        c for c in golden()["cases"] if c["symbol"] == symbol and c["date"] == day.isoformat()
    )
    return [
        (datetime.fromisoformat(r[0]), *(rupees_to_paisa(Decimal(str(x))) for x in r[1:5]), r[5])
        for r in case["bars"][kite_tf]
    ]


@pytest.mark.parametrize("symbol", ["NSE:NIFTY 50", "NSE:RELIANCE"])
@pytest.mark.parametrize("day", [NORMAL, MUHURAT])
@pytest.mark.parametrize("tf", list(KITE_TF))
async def test_derivation_reproduces_kite_native_bars_exactly(
    loaded: Any, symbol: str, day: date, tf: str
) -> None:
    ours = [
        (b.bucket, b.open, b.high, b.low, b.close, b.volume)
        for b in await derive_bars(loaded, symbol, tf, day, day)
    ]
    assert ours == _native(symbol, day, KITE_TF[tf])


async def test_30m_and_1h_anchor_to_session_open_not_midnight(loaded: Any) -> None:
    for tf, expected in {
        "30m": [time(9, 15), time(9, 45)],
        "1h": [time(9, 15), time(10, 15)],
    }.items():
        bars = await derive_bars(loaded, "NSE:NIFTY 50", tf, NORMAL, NORMAL)
        assert [b.bucket.astimezone(IST).time() for b in bars[:2]] == expected
        assert bars[-1].bucket.astimezone(IST).time() == time(15, 15)
        assert all(b.anchored for b in bars)
    # the muhurat session opened at 18:00 — a fixed 09:15 origin would split this hour
    hour = await derive_bars(loaded, "NSE:NIFTY 50", "1h", MUHURAT, MUHURAT)
    assert [(b.bucket.astimezone(IST).time(), b.minutes) for b in hour] == [(time(18, 0), 60)]


async def test_midnight_default_would_be_wrong(loaded: Any) -> None:
    """Documents the bug the session origin prevents."""
    first = await loaded.fetchval(
        """SELECT min(time_bucket('30 minutes', ts)) FROM app.ohlcv_1m m
           JOIN app.instrument_ids i USING (sid)
           WHERE i.symbol = 'NSE:NIFTY 50' AND ts >= $1 AND ts < $2""",
        datetime.combine(NORMAL, time.min, IST),
        datetime.combine(NORMAL, time(23, 59), IST),
    )
    assert first.astimezone(IST).time() == time(9, 0)  # misaligned partial — never used


async def test_partial_last_bucket_reports_its_minute_count(loaded: Any) -> None:
    bars = await derive_bars(loaded, "NSE:NIFTY 50", "30m", NORMAL, NORMAL)
    assert bars[-1].minutes == 15  # 15:15-15:29: the session ends mid-bucket
    rel = await derive_bars(loaded, "NSE:RELIANCE", "30m", NORMAL, NORMAL)
    assert rel[-1].bucket.astimezone(IST).time() == time(14, 45)  # RELIANCE's real hole


async def test_unsupported_timeframe_rejected(loaded: Any) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        await derive_bars(loaded, "NSE:NIFTY 50", "7m", NORMAL, NORMAL)


async def test_completeness_is_calendar_aware(loaded: Any) -> None:
    by = {c.symbol: c for c in await completeness(loaded)}
    nifty, rel = by["NSE:NIFTY 50"], by["NSE:RELIANCE"]
    assert (nifty.complete, nifty.short, nifty.unknown) == (2, 0, 0)  # 375 day + 60-bar muhurat
    # RELIANCE: muhurat 60/60 complete; 2026-11-18 is a real 15-minute gap → flagged, not hidden
    assert (rel.complete, rel.short, rel.missing_minutes) == (1, 1, 15)
    review = await review_sessions(loaded, "NSE:RELIANCE")
    assert review == [
        {
            "date": "2026-11-18",
            "bars": 360,
            "expected": 375,
            "status": "short",
            "first": "2026-11-18T03:45:00+00:00",
            "last": "2026-11-18T09:44:00+00:00",
        }
    ]
    expected = await loaded.fetch(
        "SELECT session_date, expected_minutes FROM app.exchange_sessions ORDER BY 1"
    )
    assert [(r["session_date"], r["expected_minutes"]) for r in expected] == [
        (MUHURAT, 60),
        (NORMAL, 375),
    ]


async def test_unknown_when_benchmark_has_no_data(loaded: Any, test_dsn: str) -> None:
    await loaded.execute(
        """DELETE FROM app.intraday_sessions WHERE session_date = $1 AND sid =
           (SELECT sid FROM app.instrument_ids WHERE symbol = 'NSE:NIFTY 50')""",
        MUHURAT,
    )
    await refresh_exchange_sessions(
        loaded, build_settings(database_url=test_dsn), datetime(2026, 11, 19, tzinfo=IST)
    )
    rel = next(c for c in await completeness(loaded) if c.symbol == "NSE:RELIANCE")
    assert rel.unknown == 1  # not guessed as complete or short
    assert rel.complete == 0
