"""Real verified-data -> signals -> engine -> ledger -> HTTP fixture on _test only."""

import json
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.backtest.service import run_study
from app.backtest.strategies import DAILY_LONG_BASELINES
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.data.instruments import UniverseMember, sync_universe
from app.db.migrate import migrate
from app.main import create_app
from app.sandbox.parameters import PaperParameters
from tests.conftest import TEST_TOKEN, build_settings
from tests.fixtures.clock import FrozenClock

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 24, 10, tzinfo=MARKET_TZ)
FIRST = date(2024, 1, 1)
START = FIRST + timedelta(days=260)
END = START + timedelta(days=3)
SYMBOL = "NSE:AAA"


async def seed(db: Any) -> None:
    await migrate(db)
    await sync_universe(
        db,
        [UniverseMember(SYMBOL, 101, "equity"), UniverseMember("NSE:NIFTY 50", 10, "index")],
        NOW,
    )
    days = [FIRST + timedelta(days=i) for i in range(264)]
    await db.executemany(
        "INSERT INTO app.nse_archive_files(trade_date,status,fetched_at) VALUES($1,'ok',$2)",
        [(d, NOW) for d in days],
    )
    # A genuine year-range breakout, completed at START close. Stop on the third study day.
    prices = [10000] * 260 + [12000, 12000, 11000, 11000]
    await db.executemany(
        """INSERT INTO app.nse_bhav
           (symbol,trade_date,prev_close,open,high,low,close,volume)
           VALUES($1,$2,$3,$3,$3,$3,$3,10000)""",
        [(SYMBOL, d, p) for d, p in zip(days, prices, strict=True)],
    )
    await db.executemany(
        """INSERT INTO app.ohlcv_candles(symbol,timeframe,ts,open,high,low,close,volume)
           VALUES('NSE:NIFTY 50','1d',$1,10000,10000,10000,10000,10000)""",
        [(canonical_daily_ts(d, MARKET_TZ),) for d in days],
    )


async def test_real_signals_persisted_results_api_and_failure_boundaries(
    db: Any,
    test_dsn: str,
) -> None:
    await seed(db)
    settings = build_settings(
        database_url=test_dsn,
        analytics_warmup_bars=260,
        paper=PaperParameters(slippage_bps=0, volume_bps=10000),
    )
    app = create_app(settings, FrozenClock(NOW))
    payload = {
        "identifier": "year_breakout_long_v1",
        "symbol": SYMBOL,
        "start_day": str(START),
        "end_day": str(END),
        "capital_paisa": 1_000_000,
        "quantity": 10,
    }
    auth = {"Authorization": f"Bearer {TEST_TOKEN}"}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.post("/api/backtest/runs", json=payload)).status_code == 401
        assert len((await client.get("/api/backtest/strategies")).json()["strategies"]) == 5
        made = await client.post("/api/backtest/runs", json=payload, headers=auth)
        assert made.status_code == 201, made.text
        result = made.json()
        assert result["run"]["status"] == "completed"
        assert result["metrics"]["closed_trades"] == 1
        assert [f["trade_day"] for f in result["fills"]] == [
            str(START + timedelta(days=1)),
            str(START + timedelta(days=2)),
        ]
        assert result["fills"][1]["reason"] == "stop_hit"
        assert len(result["equity"]) == 4
        assert len(result["run"]["assumptions"]["input_sha256"]) == 64
        assert result["run"]["result_state"]["quantity"] == 0
        path = f"/api/backtest/runs/{result['run']['id']}"
        assert (await client.get(path)).json() == result
        listing = (await client.get("/api/backtest/runs")).json()["runs"]
        assert listing[0]["net_paisa"] == result["metrics"]["net_paisa"]
        assert (await client.get("/api/backtest/runs/999999")).status_code == 404
        assert (
            await client.post(
                "/api/backtest/runs", json={**payload, "identifier": "bad"}, headers=auth
            )
        ).status_code == 422
        for spec in DAILY_LONG_BASELINES[1:]:
            run_id = await run_study(
                db,
                identifier=spec.id,
                symbol=SYMBOL,
                start_day=START,
                end_day=END,
                capital_paisa=1_000_000,
                quantity=10,
                settings=settings,
                now=NOW,
            )
            status = await db.fetchval("SELECT status FROM app.backtest_runs WHERE id=$1", run_id)
            assert status == "completed"
        # Missing middle equity session must fail, never fill on the next *available* day.
        await db.execute(
            "DELETE FROM app.nse_bhav WHERE symbol=$1 AND trade_date=$2",
            SYMBOL,
            START + timedelta(days=1),
        )
        failed = (await client.post("/api/backtest/runs", json=payload, headers=auth)).json()
        assert failed["run"]["status"] == "failed"
        assert "alignment" in failed["run"]["detail"]
        assert failed["fills"] == []
        assert failed["metrics"] is None
        await seed_missing(db)
        await db.execute(
            """INSERT INTO app.corporate_actions(symbol,series,ex_date,purpose,kinds,first_seen)
               VALUES($1,'EQ',$2,'SPLIT',ARRAY['split'],$2)""",
            SYMBOL,
            START,
        )
        refused = (await client.post("/api/backtest/runs", json=payload, headers=auth)).json()
        assert refused["run"]["status"] == "failed"
        assert "corporate actions" in refused["run"]["detail"]
        # Completed run cannot be overwritten.
        from app.backtest.engine import run_daily_long
        from app.backtest.history import verified_daily_history
        from app.backtest.metrics import performance
        from app.backtest.store import save_result
        from app.backtest.strategies import DAILY_LONG_BASELINES as specs

        bars, _ = await verified_daily_history(db, SYMBOL, START, END)
        replay = run_daily_long(bars, [], specs[0], SYMBOL, START, 1_000_000, 10, settings.paper)
        with pytest.raises(ValueError, match="writable"):
            await save_result(db, result["run"]["id"], replay, performance(replay, 1_000_000))
        assumptions = json.loads(
            await db.fetchval(
                "SELECT assumptions FROM app.backtest_runs WHERE id=$1", result["run"]["id"]
            )
        )
        assert assumptions["input_sha256"] == result["run"]["assumptions"]["input_sha256"]


async def seed_missing(db: Any) -> None:
    await db.execute(
        """INSERT INTO app.nse_bhav(symbol,trade_date,prev_close,open,high,low,close,volume)
           VALUES($1,$2,12000,12000,12000,12000,12000,10000)""",
        SYMBOL,
        START + timedelta(days=1),
    )
