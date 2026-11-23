"""Phase 2 read API with stored Phase 1 candles and raw option snapshots on TimescaleDB."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import httpx
import pytest

from app.analytics.chain import years_to_expiry
from app.analytics.options import Model, OptionType
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.data.instruments import UniverseMember, sync_universe
from app.db.migrate import migrate
from app.main import create_app
from tests.conftest import build_settings
from tests.fixtures.candles import Pattern, make_daily_candles
from tests.fixtures.clock import FrozenClock

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 21, 10, 0, tzinfo=MARKET_TZ)
SYMBOL = "NSE:AAA"
INDEX = "NSE:NIFTY 50"


async def test_technical_api_warmup_quality_finality_and_paging(db: Any, test_dsn: str) -> None:
    await migrate(db)
    await sync_universe(
        db, [UniverseMember(SYMBOL, 101, "equity"), UniverseMember(INDEX, 10, "index")], NOW
    )
    history = make_daily_candles(Pattern.CHOP, n=260)
    await db.executemany(
        """INSERT INTO app.ohlcv_candles (symbol,timeframe,ts,open,high,low,close,volume)
           VALUES ($1,'1d',$2,$3,$4,$5,$6,$7)""",
        [
            (symbol, b.ts, b.open, b.high, b.low, b.close, b.volume)
            for symbol in (SYMBOL, INDEX)
            for b in history
        ],
    )
    first, second = [date(2026, 11, d) for d in (19, 20)]
    sid = await db.fetchval(
        "INSERT INTO app.instrument_ids (symbol) VALUES ($1) RETURNING sid", SYMBOL
    )
    for day, quality in [(first, "complete"), (second, "short")]:
        opening = datetime.combine(day, time(9, 15), MARKET_TZ)
        await db.execute(
            """INSERT INTO app.exchange_sessions
               (exchange, session_date, session_open, benchmark, refreshed_at)
               VALUES ('NSE',$1,$2,$3,$2)""",
            day,
            opening,
            INDEX,
        )
        await db.execute(
            """INSERT INTO app.intraday_sessions
               (sid, session_date, bars, first_ts, last_ts,
                open_first, high_max, low_min, volume_sum)
               VALUES ($1,$2,2,$3,$4,10000,10010,9990,20)""",
            sid,
            day,
            opening,
            opening + timedelta(minutes=1),
        )
        await db.execute(
            """INSERT INTO app.intraday_session_status
               (sid, session_date, bars, expected, status) VALUES ($1,$2,2,$3,$4)""",
            sid,
            day,
            2 if quality == "complete" else 3,
            quality,
        )
        await db.executemany(
            """INSERT INTO app.ohlcv_1m (sid, ts, open, high, low, close, volume)
               VALUES ($1,$2,10000,10010,9990,10000,10)""",
            [(sid, opening + timedelta(minutes=i)) for i in range(2)],
        )
    # This Saturday candle is still provisional on Saturday morning.
    await db.execute(
        """INSERT INTO app.ohlcv_candles (symbol,timeframe,ts,open,high,low,close,volume)
           VALUES ($1,'1d',$2,999,999,999,999,0)""",
        SYMBOL,
        canonical_daily_ts(NOW.date(), MARKET_TZ),
    )
    clock = FrozenClock(NOW)
    app = create_app(build_settings(database_url=test_dsn), clock=clock)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        path = "/api/analytics/indicators"
        result = await client.get(path, params={"symbol": SYMBOL, "count": 2})
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["status"] == "ok"
        assert body["rows"][-1]["t"] == int(history[-1].ts.timestamp())
        assert body["rows"][-1]["values"]["ema_200_paisa"] > 0
        assert body["rows"][-1]["values"]["relative_strength_pct"] == 0
        early = await client.get(
            path,
            params={
                "symbol": SYMBOL,
                "before": (history[199].ts + timedelta(hours=10)).isoformat(),
            },
        )
        point = early.json()["rows"][-1]
        assert point["usable_bars"] == 199
        assert point["values"]["ema_200_paisa"] is None
        intraday = await client.get(path, params={"symbol": SYMBOL, "interval": "1m", "count": 4})
        assert [r["status"] for r in intraday.json()["rows"]] == [
            "warming_up",
            "warming_up",
            "intraday_session_short",
            "intraday_session_short",
        ]
        assert intraday.json()["rows"][1]["values"]["vwap_paisa"] == 10_000
        assert (await client.get(path, params={"symbol": "NSE:ABSENT"})).status_code == 404
        assert (
            await client.get(path, params={"symbol": SYMBOL, "before": "2026-11-20T09:00:00"})
        ).status_code == 422


async def _options(db: Any, when: datetime, iv: float, oi: int, status: str = "ok") -> None:
    spot = 1_000_000
    expiries = (date(2026, 12, 10), date(2027, 1, 14))
    kinds: tuple[OptionType, ...] = ("CE", "PE")
    values: list[tuple[datetime, int, date, int, OptionType, int, int, int]] = []
    for expiry in expiries:
        for opt_type in kinds:
            for strike in (spot - 10_000, spot, spot + 10_000):
                years = years_to_expiry(expiry, when, True)
                model = Model(spot, strike, years, 0.06, 0.01, opt_type)
                midpoint = round(model.price(iv))
                values.append(
                    (
                        when,
                        len(values) + 1,
                        expiry,
                        strike,
                        opt_type,
                        midpoint - 1,
                        midpoint + 1,
                        oi,
                    )
                )
    await db.execute(
        """INSERT INTO app.option_snapshot_meta
           (snapshot_ts, underlying, spot_paisa, contracts, status) VALUES ($1,'NIFTY',$2,$3,$4)""",
        when,
        spot,
        len(values),
        status,
    )
    await db.executemany(
        """INSERT INTO app.option_snapshots
           (snapshot_ts, underlying, instrument_token, expiry, strike_paisa,
            opt_type, bid, ask, bid_qty, ask_qty, oi, volume)
           VALUES ($1,'NIFTY',$2,$3,$4,$5,$6,$7,2,2,$8,10)""",
        values,
    )


async def test_options_api_uses_snapshots_available_as_of_and_preserves_raw_rows(
    db: Any, test_dsn: str
) -> None:
    await migrate(db)
    now = datetime(2026, 11, 20, 15, 0, tzinfo=MARKET_TZ)
    await _options(db, now - timedelta(days=2), 0.1, 8)
    await _options(db, now - timedelta(days=1), 0.3, 9)
    await _options(db, now, 0.2, 10)
    await _options(db, now + timedelta(minutes=5), 0.4, 13, "partial")
    before = await db.fetchval("SELECT count(*) FROM app.option_snapshots")
    app = create_app(
        build_settings(database_url=test_dsn, analytics_iv_min_observations=3),
        clock=FrozenClock(NOW),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        path = "/api/analytics/options"
        params = {"underlying": "NIFTY", "as_of": now.isoformat()}
        response = await client.get(
            path, params={**params, "rate_bps": "600", "dividend_bps": "100"}
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "ok"
        assert result["snapshot_age_seconds"] == 0
        assert result["iv_rank"]["current_iv_pct"] == pytest.approx(20, abs=0.002)
        assert result["iv_rank"]["rank_pct"] == pytest.approx(50, abs=0.02)
        [near, _far] = result["expiries"]
        assert near["pcr_oi"] == 1
        assert near["gamma_wall"]["gross_gamma_exposure_paisa_per_1pct"] > 0
        assert near["contracts"][0]["oi_change"] == 1
        without_model = (await client.get(path, params=params)).json()
        assert without_model["expiries"][0]["pcr_oi"] == 1
        assert without_model["expiries"][0]["contracts"][0]["greeks"] is None
        latest = (await client.get(path, params={"underlying": "NIFTY"})).json()
        assert latest["status"] == "partial"
        assert latest["stale"] is True
        assert latest["expiries"][0]["pcr_oi"] is None
        assert (await client.get(path, params={"underlying": "ABSENT"})).json()[
            "status"
        ] == "no_snapshot"
    assert await db.fetchval("SELECT count(*) FROM app.option_snapshots") == before
