"""Chart data API (read-only): symbols + paged candles per interval, adjusted vs as-traded."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.data.instruments import UniverseMember, sync_universe
from app.db.migrate import migrate
from app.main import create_app
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock

pytestmark = pytest.mark.db

IST = MARKET_TZ
NOW = datetime(2026, 11, 20, 9, 0, tzinfo=IST)
DAYS = [date(2026, 11, d) for d in (9, 10, 11, 12, 13, 16, 17, 18, 19)]  # Mon..Fri, Mon..Thu


def epoch(d: datetime) -> int:
    return int(d.timestamp())


async def _seed(db: Any) -> int:
    await migrate(db)
    await sync_universe(
        db,
        [UniverseMember("NSE:AAA", 101, "equity"), UniverseMember("NSE:NIFTY 50", 256265, "index")],
        NOW,
    )
    for i, d in enumerate(DAYS):
        k = 10_000 + i * 100  # Kite (adjusted) daily
        await db.execute(
            """INSERT INTO app.ohlcv_candles (symbol, timeframe, ts, open, high, low, close, volume)
               VALUES ('NSE:AAA', '1d', $1, $2, $3, $4, $5, $6)""",
            canonical_daily_ts(d, IST),
            k,
            k + 50,
            k - 50,
            k + 10,
            1000 + i,
        )
        await db.execute(
            """INSERT INTO app.nse_bhav
               (symbol, trade_date, prev_close, open, high, low, close, volume)
               VALUES ('NSE:AAA', $1, $2, $2, $3, $4, $5, $6)""",
            d,
            2 * k,
            2 * k + 100,
            2 * k - 100,
            2 * k + 20,
            500 + i,  # official = 2x Kite
        )
    sid = await db.fetchval(
        "INSERT INTO app.instrument_ids (symbol) VALUES ('NSE:AAA') RETURNING sid"
    )
    # two sessions of 1m bars (09:15..09:44), session opens in the calendar
    for d in (DAYS[-2], DAYS[-1]):
        open_ = datetime.combine(d, datetime.min.time(), IST).replace(hour=9, minute=15)
        await db.execute(
            """INSERT INTO app.exchange_sessions
               (exchange, session_date, session_open, expected_minutes, benchmark, refreshed_at)
               VALUES ('NSE', $1, $2, NULL, 'n/a', $2)""",
            d,
            open_,
        )
        await db.executemany(
            "INSERT INTO app.ohlcv_1m (ts, volume, open, high, low, close, sid) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            [
                (
                    open_ + timedelta(minutes=m),
                    10,
                    10_000 + m,
                    10_005 + m,
                    9_995 + m,
                    10_002 + m,
                    sid,
                )
                for m in range(30)
            ],
        )
        await db.execute(  # the extractor maintains this per session (see extract_intraday)
            """INSERT INTO app.intraday_sessions (sid, session_date, bars, first_ts, last_ts)
               VALUES ($1, $2, 30, $3, $4)""",
            sid,
            d,
            open_,
            open_ + timedelta(minutes=29),
        )
    # measured adjustment for the 1m as-traded view: day -2 usable (x0.5), day -1 unusable
    await db.executemany(
        """INSERT INTO app.adjustment_days
           (symbol, trade_date, window_no, f_daily, b_daily, g_minute, v_daily, v_minute, quality)
           VALUES ('NSE:AAA', $1, 0, $2, 0, 1, 1, 1, $3)""",
        [(DAYS[-2], 0.5, "measured"), (DAYS[-1], 0.5, "suspect")],
    )
    return int(sid)


@pytest.fixture
async def client(db: Any, test_dsn: str) -> Any:
    await _seed(db)
    app = create_app(
        build_settings(database_url=test_dsn, chart_max_bars=1000), clock=FrozenClock(NOW)
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield c


async def test_symbols_lists_the_universe(client: Any) -> None:
    r = (await client.get("/api/data/symbols")).json()
    assert {(s["symbol"], s["exchange"], s["tradingsymbol"], s["kind"]) for s in r["symbols"]} == {
        ("NSE:AAA", "NSE", "AAA", "equity"),
        ("NSE:NIFTY 50", "NSE", "NIFTY 50", "index"),
    }


async def test_daily_adjusted_pages_backwards(client: Any) -> None:
    first = (
        await client.get(
            "/api/data/candles", params={"symbol": "NSE:AAA", "interval": "D", "count": 4}
        )
    ).json()
    assert first["adjustment"] == "adjusted"
    assert [b["t"] for b in first["bars"]] == [epoch(canonical_daily_ts(d, IST)) for d in DAYS[-4:]]
    assert first["bars"][-1]["c"] == 10_000 + 8 * 100 + 10  # paisa, as stored
    assert first["has_more"] is True
    older = (
        await client.get(
            "/api/data/candles",
            params={
                "symbol": "NSE:AAA",
                "interval": "D",
                "count": 10,
                "before": first["next_before"],
            },
        )
    ).json()
    assert [b["t"] for b in older["bars"]] == [epoch(canonical_daily_ts(d, IST)) for d in DAYS[:-4]]
    assert older["has_more"] is False


async def test_daily_raw_is_nse_official(client: Any) -> None:
    r = (
        await client.get(
            "/api/data/candles",
            params={"symbol": "NSE:AAA", "interval": "D", "adjustment": "raw", "count": 2},
        )
    ).json()
    assert [b["c"] for b in r["bars"]] == [2 * (10_000 + 7 * 100) + 20, 2 * (10_000 + 8 * 100) + 20]
    assert r["source"] == "NSE official bhavcopy"


async def test_weekly_is_built_server_side_from_daily(client: Any) -> None:
    r = (
        await client.get(
            "/api/data/candles", params={"symbol": "NSE:AAA", "interval": "W", "count": 5}
        )
    ).json()
    first_week, second_week = r["bars"]
    assert first_week["t"] == epoch(datetime(2026, 11, 9, tzinfo=IST))  # Monday 00:00 IST
    assert (first_week["o"], first_week["c"]) == (
        10_000,
        10_000 + 4 * 100 + 10,
    )  # Mon open, Fri close
    assert first_week["h"] == 10_000 + 4 * 100 + 50
    assert first_week["v"] == sum(1000 + i for i in range(5))
    assert second_week["t"] == epoch(datetime(2026, 11, 16, tzinfo=IST))


async def test_minute_adjusted_and_derived_use_the_backend_derivation(client: Any) -> None:
    one = (
        await client.get(
            "/api/data/candles", params={"symbol": "NSE:AAA", "interval": "1m", "count": 3}
        )
    ).json()
    assert [b["c"] for b in one["bars"]] == [10_002 + m for m in (27, 28, 29)]
    five = (
        await client.get(
            "/api/data/candles", params={"symbol": "NSE:AAA", "interval": "5m", "count": 100}
        )
    ).json()
    assert len(five["bars"]) == 12  # 6 buckets x 2 sessions, anchored to 09:15
    assert five["bars"][0]["t"] == epoch(
        datetime.combine(DAYS[-2], datetime.min.time(), IST).replace(hour=9, minute=15)
    )
    assert five["bars"][0]["v"] == 50


async def test_minute_raw_is_measured_and_omits_unusable_days(client: Any) -> None:
    r = (
        await client.get(
            "/api/data/candles",
            params={"symbol": "NSE:AAA", "interval": "1m", "adjustment": "raw", "count": 100},
        )
    ).json()
    assert len(r["bars"]) == 30  # only the 'measured' day
    assert r["bars"][0]["c"] == 2 * 10_002  # (Kite / g - b) / a with a = 0.5
    assert r["omitted_unusable"] == 30  # the 'suspect' day: never guessed, reported


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"symbol": "NSE:AAA", "interval": "5m", "adjustment": "raw"}, 422),  # not offered
        ({"symbol": "NSE:AAA", "interval": "2m"}, 422),
        ({"symbol": "NSE:AAA", "interval": "D", "adjustment": "split"}, 422),
        ({"symbol": "NSE:AAA", "interval": "D", "count": 0}, 422),
        ({"symbol": "NSE:AAA", "interval": "D", "count": 5000}, 422),  # over CHART_MAX_BARS
        ({"symbol": "NSE:ZZZ", "interval": "D"}, 404),
    ],
)
async def test_bad_requests_are_refused_not_guessed(
    client: Any, params: dict[str, Any], status: int
) -> None:
    assert (await client.get("/api/data/candles", params=params)).status_code == status


async def test_adjusted_view_applies_recorded_kite_drift(client: Any, db: Any) -> None:
    await db.execute(
        """INSERT INTO app.adjustment_drift (symbol, timeframe, ref_date, ratio, detected_at)
           VALUES ('NSE:AAA', '1d', $1, 0.5, $2)""",
        DAYS[-2],
        NOW,
    )
    r = (
        await client.get(
            "/api/data/candles", params={"symbol": "NSE:AAA", "interval": "D", "count": 2}
        )
    ).json()
    assert [b["c"] for b in r["bars"]] == [
        round((10_000 + 7 * 100 + 10) * 0.5),
        10_000 + 8 * 100 + 10,
    ]


async def test_pre_listing_ghost_bars_are_never_charted(client: Any, db: Any) -> None:
    await db.execute(
        """INSERT INTO app.ohlcv_flags (symbol, timeframe, ts, flag, detail, flagged_at)
           VALUES ('NSE:AAA', '1d', $1, 'pre_listing', 'ghost', $2)""",
        canonical_daily_ts(DAYS[0], IST),
        NOW,
    )
    r = (
        await client.get(
            "/api/data/candles", params={"symbol": "NSE:AAA", "interval": "D", "count": 100}
        )
    ).json()
    assert epoch(canonical_daily_ts(DAYS[0], IST)) not in [b["t"] for b in r["bars"]]
    assert len(r["bars"]) == len(DAYS) - 1


async def test_a_page_of_only_omitted_minutes_still_pages_on(client: Any) -> None:
    """The cursor is the earliest bar scanned, not kept: an all-omitted page still pages on."""
    first = (
        await client.get(
            "/api/data/candles",
            params={"symbol": "NSE:AAA", "interval": "1m", "adjustment": "raw", "count": 30},
        )
    ).json()
    newest_open = datetime.combine(DAYS[-1], datetime.min.time(), IST).replace(hour=9, minute=15)
    assert (first["bars"], first["omitted_unusable"]) == ([], 30)
    assert (first["has_more"], first["next_before"]) == (True, epoch(newest_open))
    older = (
        await client.get(
            "/api/data/candles",
            params={
                "symbol": "NSE:AAA",
                "interval": "1m",
                "adjustment": "raw",
                "count": 30,
                "before": first["next_before"],
            },
        )
    ).json()
    assert len(older["bars"]) == 30
    assert older["has_more"] is False
