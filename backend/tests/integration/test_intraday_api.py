"""1b end-to-end: real app + real KiteClient over MockTransport, serving real Kite 1m bars."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.core.tradingdate import MARKET_TZ
from app.data.instruments import UniverseMember, sync_universe
from app.db.migrate import migrate
from app.main import create_app
from tests.conftest import TEST_TOKEN, build_settings
from tests.fixtures.clock import FrozenClock
from tests.fixtures.kite_http import KITE_KEY, KITE_SECRET, FakeKiteHTTP
from tests.fixtures.minutes import golden

pytestmark = pytest.mark.db

IST = MARKET_TZ
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
NOW = datetime(2026, 11, 18, 18, 0, tzinfo=IST)


class _KiteMinutes(FakeKiteHTTP):
    def __init__(self) -> None:
        super().__init__()
        self.by_token: dict[int, list[list[Any]]] = {}
        for case in golden()["cases"]:
            self.by_token.setdefault(case["instrument_token"], []).extend(case["bars"]["minute"])

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/instruments/historical/"):
            self.requests.append(request)
            _, _, _, token, interval = request.url.path.split("/")
            assert interval == "minute"
            lo = datetime.fromisoformat(request.url.params["from"]).replace(tzinfo=IST)
            hi = datetime.fromisoformat(request.url.params["to"]).replace(tzinfo=IST)
            rows = [
                r
                for r in self.by_token.get(int(token), [])
                if lo <= datetime.fromisoformat(r[0]) <= hi
            ]
            # Kite sends JSON numbers; the golden file stores them as exact decimal strings
            body = json.dumps(
                {
                    "status": "success",
                    "data": {
                        "candles": [
                            [r[0], *(json.loads(x) if isinstance(x, str) else x for x in r[1:])]
                            for r in rows
                        ]
                    },
                }
            )
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})
        return super().handler(request)


async def test_intraday_ingest_bars_and_completeness_end_to_end(db: Any, test_dsn: str) -> None:
    await migrate(db)
    await sync_universe(
        db,
        [
            UniverseMember("NSE:NIFTY 50", 256265, "index"),
            UniverseMember("NSE:RELIANCE", 738561, "equity"),
        ],
        NOW,
    )
    await db.execute(
        """INSERT INTO app.kite_session (id, user_id, access_token, login_time, expires_at)
           VALUES (1, 'AB1234', 'acc_tok', $1, $2)""",
        NOW - timedelta(hours=1),
        NOW + timedelta(hours=10),
    )
    settings = build_settings(
        database_url=test_dsn,
        kite_api_key=KITE_KEY,
        kite_api_secret=KITE_SECRET,
        kite_historical_rps=1000.0,
        intraday_floor_date=date(2024, 10, 1),
        kite_window_days={"minute": 1000},
    )
    app = create_app(settings, clock=FrozenClock(NOW), http_transport=_KiteMinutes().transport())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            assert (await c.post("/api/data/ingest/intraday")).status_code == 401
            r = await c.post("/api/data/ingest/intraday", headers=AUTH)
            assert r.status_code == 202
            await app.state.ingest.wait()
            job = (await c.get("/api/data/jobs")).json()["last"]
            assert job["name"] == "intraday_ingest"
            assert job["status"] == "ok", job
            assert job["result"]["intraday"]["inserted"] == 375 + 360 + 60 + 60

            bars = (
                await c.get(
                    "/api/data/bars",
                    params={
                        "symbol": "NSE:NIFTY 50",
                        "timeframe": "30m",
                        "start": "2026-11-18",
                        "end": "2026-11-18",
                    },
                )
            ).json()["bars"]
            assert bars[0]["t"] == "2026-11-18T03:45:00+00:00"  # 09:15 IST
            assert bars[-1]["minutes"] == 15
            assert all(b["anchored"] for b in bars)
            bad = await c.get(
                "/api/data/bars",
                params={
                    "symbol": "NSE:NIFTY 50",
                    "timeframe": "7m",
                    "start": "2026-11-18",
                    "end": "2026-11-18",
                },
            )
            assert bad.status_code == 422

            cov = (
                await c.get("/api/data/coverage/intraday", params={"review": "NSE:RELIANCE"})
            ).json()
            assert cov["summary"]["minute_bars"] == 855
            assert cov["summary"]["short_sessions"] == 1
            assert cov["summary"]["missing_minutes"] == 15
            assert cov["review"]["sessions"][0]["status"] == "short"
