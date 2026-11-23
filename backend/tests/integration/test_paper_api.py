from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.core.tradingdate import MARKET_TZ
from app.data.instruments import UniverseMember, sync_universe
from app.db.migrate import migrate
from app.main import create_app
from app.sandbox.parameters import PaperParameters
from tests.conftest import TEST_TOKEN, build_settings
from tests.fixtures.clock import FrozenClock

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 21, 10, tzinfo=MARKET_TZ)
FIRST = date(2026, 11, 18)
SECOND = date(2026, 11, 19)


async def test_replay_auth_idempotency_controls_partial_fill_and_pnl(
    db: Any, test_dsn: str
) -> None:
    await migrate(db)
    symbol = "NSE:AAA"
    await sync_universe(db, [UniverseMember(symbol, 101, "equity")], NOW)
    await db.executemany(
        """INSERT INTO app.nse_archive_files
           (trade_date,status,pd_rows,fetched_at) VALUES ($1,'ok',1,$2)""",
        [(FIRST, NOW), (SECOND, NOW)],
    )
    await db.executemany(
        """INSERT INTO app.nse_bhav
           (symbol,trade_date,prev_close,open,high,low,close,volume)
           VALUES ($1,$2,10000,$3,$3,$3,$3,$4)""",
        [(symbol, FIRST, 10000, 200), (symbol, SECOND, 11000, 300)],
    )
    settings = build_settings(
        database_url=test_dsn,
        paper=PaperParameters(slippage_bps=0, volume_bps=1000),
    )
    app = create_app(settings, clock=FrozenClock(NOW))
    auth = {"Authorization": f"Bearer {TEST_TOKEN}"}
    data = {"name": "Replay fixture", "start_day": FIRST.isoformat(), "mode": "learning"}
    base = "/api/paper/accounts"
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.post(base, json=data)).status_code == 401
        made = await client.post(base, json=data, headers=auth)
        assert made.status_code == 201, made.text
        path = f"{base}/{made.json()['id']}"
        request_id = str(uuid4())
        instruction = {"symbol": symbol, "side": "buy", "kind": "market", "quantity": 30}
        order = {"request_id": request_id, "instruction": instruction}
        created = await client.post(f"{path}/orders", json=order, headers=auth)
        assert created.status_code == 201, created.text
        retry = await client.post(f"{path}/orders", json=order, headers=auth)
        assert retry.json()["id"] == created.json()["id"]
        bad = {"request_id": request_id, "instruction": {**instruction, "quantity": 31}}
        assert (await client.post(f"{path}/orders", json=bad, headers=auth)).status_code == 409
        ctl = "/api/paper/controls"
        assert (
            await client.put(ctl, json={"paused": True, "killed": True}, headers=auth)
        ).status_code == 200
        assert (
            await client.post(f"{path}/advance", json={"expected_day": None}, headers=auth)
        ).status_code == 409
        assert (await client.get(base)).json()["controls"] == {"paused": True, "killed": True}
        await client.put(ctl, json={"paused": False, "killed": False}, headers=auth)
        step = await client.post(f"{path}/advance", json={"expected_day": None}, headers=auth)
        assert step.status_code == 200, step.text
        assert (
            await client.post(f"{path}/advance", json={"expected_day": None}, headers=auth)
        ).status_code == 409
        after_buy = (await client.get(path)).json()
        assert after_buy["positions"][0]["quantity"] == 20
        assert after_buy["orders"][0]["status"] == "cancelled"
        assert after_buy["orders"][0]["filled"] == 20
        assert after_buy["fills"][0]["fees_paisa"] == 308
        assert after_buy["account"]["cash_paisa"] == 1_000_000_000 - 200_308
        sell = {
            "request_id": str(uuid4()),
            "instruction": {**instruction, "side": "sell", "quantity": 20},
        }
        assert (await client.post(f"{path}/orders", json=sell, headers=auth)).status_code == 201
        step = await client.post(
            f"{path}/advance", json={"expected_day": FIRST.isoformat()}, headers=auth
        )
        assert step.status_code == 200, step.text
        outcome = (await client.get(path)).json()
        assert outcome["positions"] == []
        assert outcome["fills"][0]["fees_paisa"] == 306
        assert outcome["account"]["realized_paisa"] == 19_386
        assert outcome["equity_paisa"] == 1_000_019_386
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get(path)).json()["equity_paisa"] == 1_000_019_386
