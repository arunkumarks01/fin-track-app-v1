from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest

from app.core.tradingdate import MARKET_TZ
from app.data.instruments import UniverseMember, sync_universe
from app.db.migrate import migrate
from app.main import create_app
from app.risk.parameters import RiskParameters
from app.scoring.calibration import model_key
from app.scoring.scans import scan_universe
from tests.conftest import TEST_TOKEN, build_settings
from tests.fixtures.candles import Pattern, make_daily_candles
from tests.fixtures.clock import FrozenClock

pytestmark = pytest.mark.db


async def test_candidates_show_real_rule_evidence_and_pin_custom_revisions(
    db: Any, test_dsn: str
) -> None:
    await migrate(db)
    history = make_daily_candles(Pattern.TREND, n=260)
    now = history[-1].ts + timedelta(days=1, hours=10)
    symbol = "NSE:AAA"
    benchmark = "NSE:NIFTY 50"
    await sync_universe(
        db, [UniverseMember(symbol, 101, "equity"), UniverseMember(benchmark, 10, "index")], now
    )
    await db.execute(
        """INSERT INTO app.instruments (
               instrument_token, exchange_token, tradingsymbol, exchange, segment, instrument_type,
               tick_size, lot_size, refreshed_at
           ) VALUES (101, 101, 'AAA', 'NSE', 'NSE', 'EQ', 5, 1, $1)""",
        now,
    )
    await db.executemany(
        """INSERT INTO app.ohlcv_candles
           (symbol, timeframe, ts, open, high, low, close, volume)
           VALUES ($1, '1d', $2, $3, $4, $5, $6, $7)""",
        [
            (ticker, b.ts, b.open, b.high, b.low, b.close, b.volume)
            for ticker in (symbol, benchmark)
            for b in history
        ],
    )
    settings = build_settings(database_url=test_dsn)
    app = create_app(settings, clock=FrozenClock(now))
    definition = {
        "id": "custom_close",
        "name": "Custom close above threshold",
        "mode": "swing",
        "direction": "long",
        "rules": [
            {
                "label": "Final close",
                "observed": "close_paisa",
                "comparison": ">",
                "threshold": history[-1].close - 1,
            }
        ],
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        path = "/api/scoring/strategies"
        payload = {"definition": definition, "enabled": True}
        assert (await client.put(path, json=payload)).status_code == 401
        headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
        assert (await client.put(path, json=payload, headers=headers)).status_code == 201
        assert (await client.put(path, json=payload, headers=headers)).status_code == 409
        result = await client.get("/api/scoring/candidates", params={"symbol": symbol})
        assert result.status_code == 200, result.text
        report = result.json()
        assert report["data_status"] == "ok"
        candidate = next(x for x in report["evaluations"] if x["id"] == "custom_close")
        assert candidate["status"] == "matched"
        assert candidate["evidence"][0]["observed"] == history[-1].close
        assert candidate["evidence"][0]["threshold"] == history[-1].close - 1
        assert candidate["conviction"]["status"] == "uncalibrated"
        plan = candidate["trade_plan"]
        levels = plan["levels"]
        assert plan["status"] == "draft"
        assert levels["stop_paisa"] < levels["trigger_paisa"] <= levels["entry_limit_paisa"]
        assert levels["entry_limit_paisa"] < levels["target_paisa"]
        assert plan["capital_paisa"] == 1_000_000_000
        assert plan["guardrails"]["blocks"] is False
        trade_day = history[-1].ts.astimezone(MARKET_TZ).date()
        account_id = await db.fetchval(
            """INSERT INTO app.paper_accounts
               (name,start_day,paper_mode,created_at,capital_paisa,cash_paisa,
                paper_parameters,risk_parameters)
               VALUES ('Risk account',$1,'realistic',$2,1000000,1,'{}'::jsonb,$3::jsonb)
               RETURNING id""",
            trade_day,
            now,
            RiskParameters(paper_capital_paisa=1_000_000, max_positions=1).model_dump_json(),
        )
        await db.execute(
            """INSERT INTO app.paper_positions
               (account_id,symbol,quantity,cost_paisa,mark_paisa,mark_day)
               VALUES ($1,'NSE:HELD',1,10000,10000,$2)""",
            account_id,
            trade_day,
        )
        account_report = await client.get(
            "/api/scoring/candidates", params={"symbol": symbol, "paper_account_id": account_id}
        )
        account_plan = next(
            x for x in account_report.json()["evaluations"] if x["id"] == "custom_close"
        )["trade_plan"]
        assert account_plan["paper_account_id"] == account_id
        assert account_plan["guardrails"]["blocks"] is True
        assert "Maximum open positions reached" in account_plan["guardrails"]["warnings"]
        await db.execute(
            """INSERT INTO app.scoring_calibrations (
                   strategy_id,strategy_revision,instrument,model_key,available_at,
                   evaluated_through,observations,wins
               ) VALUES ('custom_close', 1, 'underlying', $1, $2, $3, 40, 22)""",
            model_key(settings),
            now,
            now - timedelta(days=1),
        )
        recalculated = await client.get("/api/scoring/candidates", params={"symbol": symbol})
        calibrated = next(
            x for x in recalculated.json()["evaluations"] if x["id"] == "custom_close"
        )
        assert calibrated["conviction"]["win_probability_pct"] == 55
        assert len((await client.get(path)).json()["custom"]) == 1
        earlier = await client.get(
            "/api/scoring/candidates",
            params={
                "symbol": symbol,
                "as_of": (now - timedelta(minutes=1)).isoformat(),
            },
        )
        assert all(x["id"] != "custom_close" for x in earlier.json()["evaluations"])
        options = await client.get(
            "/api/scoring/candidates",
            params={
                "symbol": symbol,
                "instrument": "option",
            },
        )
        assert len(options.json()["evaluations"]) == 2
        assert {x["status"] for x in options.json()["evaluations"]} == {"unavailable"}
        pool = app.state.pool
        assert (await scan_universe(pool, settings, now, "swing"))["inserted"] >= 1
        assert (await scan_universe(pool, settings, now, "swing"))["skipped"]
        scan = (await client.get("/api/scoring/scans")).json()["latest"]
        assert scan["symbols_evaluated"] == 1
        assert all(item["evaluations"] for item in scan["matches"])
        assert (await client.get("/api/scoring/scans")).json()["history"] == [scan]
        overview = (await client.get("/api/admin/overview")).json()
        assert "scoring_swing" in overview["operable_jobs"]
        assert "scoring_intraday" in overview["operable_jobs"]
        assert (await client.post("/api/admin/jobs/scoring_swing/start")).status_code == 401
        started = await client.post("/api/admin/jobs/scoring_swing/start", headers=headers)
        assert started.status_code == 202
