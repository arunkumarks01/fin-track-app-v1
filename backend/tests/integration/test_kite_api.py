"""Kite login endpoints end-to-end: real app + real DB, scripted Kite over MockTransport."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.core.tradingdate import MARKET_TZ
from app.main import create_app
from tests.conftest import TEST_TOKEN, build_settings
from tests.fixtures.clock import FrozenClock
from tests.fixtures.kite_http import KITE_KEY, KITE_SECRET, FakeKiteHTTP, kite_error, kite_ok

pytestmark = pytest.mark.db

IST = MARKET_TZ
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
TOKEN_OK = kite_ok(
    {"user_id": "AB1234", "access_token": "acc_TOKEN_xyz", "login_time": "2026-11-19 09:15:00"}
)


def _settings(test_dsn: str, **kw: Any) -> Settings:
    return build_settings(
        database_url=test_dsn, kite_api_key=KITE_KEY, kite_api_secret=KITE_SECRET, **kw
    )


@asynccontextmanager
async def running(
    settings: Settings, clock: FrozenClock, fake: FakeKiteHTTP
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    app = create_app(settings, clock=clock, http_transport=fake.transport())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            yield app, client


async def _state(client: httpx.AsyncClient) -> str:
    r = await client.get("/api/kite/login-url")
    assert r.status_code == 200
    redirect = parse_qs(urlparse(r.json()["login_url"]).query)["redirect_params"][0]
    return str(parse_qs(redirect)["state"][0])


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 11, 19, 9, 16, tzinfo=IST))


async def test_not_configured_is_honest(db: Any, test_dsn: str, clock: FrozenClock) -> None:
    async with running(build_settings(database_url=test_dsn), clock, FakeKiteHTTP()) as (_, c):
        assert (await c.get("/api/kite/status")).json()["status"] == "not_configured"
        assert (await c.get("/api/kite/login-url")).status_code == 503


async def test_full_login_persist_restore_expire_logout(
    db: Any, test_dsn: str, clock: FrozenClock
) -> None:
    fake = FakeKiteHTTP()
    fake.on("POST", "/session/token", TOKEN_OK)
    fake.on("DELETE", "/session/token", kite_ok(True))
    settings = _settings(test_dsn)

    async with running(settings, clock, fake) as (_, c):
        assert (await c.get("/api/kite/status")).json()["status"] == "disconnected"
        state = await _state(c)
        r = await c.get(
            "/api/kite/callback",
            params={"request_token": "reqTOKEN456", "status": "success", "state": state},
        )
        assert r.status_code == 200
        assert "Kite connected" in r.text
        status = (await c.get("/api/kite/status")).json()
        assert status["status"] == "connected"
        assert status["user_id"] == "AB1234"
        assert status["hours_left"] == pytest.approx(20.7, abs=0.1)  # 09:16 -> 06:00
        # state is single-use
        again = await c.get(
            "/api/kite/callback", params={"request_token": "x", "status": "success", "state": state}
        )
        assert again.status_code == 400

    stored = await db.fetchrow("SELECT user_id, access_token FROM app.kite_session")
    assert (stored["user_id"], stored["access_token"]) == ("AB1234", "acc_TOKEN_xyz")

    # restart restores from Postgres
    async with running(settings, clock, fake) as (app, c):
        assert app.state.kite_sessions.current() is not None
        assert (await c.get("/api/kite/status")).json()["status"] == "connected"
        clock.set(datetime(2026, 11, 20, 6, 0, tzinfo=IST))
        assert app.state.kite_sessions.current() is None  # fail closed at expiry
        assert (await c.get("/api/kite/status")).json()["status"] == "expired"

        assert (await c.post("/api/kite/logout")).status_code == 401  # bearer required
        out = await c.post("/api/kite/logout", headers=AUTH)
        assert out.status_code == 200
        assert out.json()["remote_invalidated"] is True
        assert (await c.get("/api/kite/status")).json()["status"] == "disconnected"
    assert await db.fetchval("SELECT count(*) FROM app.kite_session") == 0
    assert fake.requests[-1].method == "DELETE"


@pytest.mark.parametrize(
    "params",
    [
        {"request_token": "t", "status": "success", "state": "forged"},
        {"request_token": "t", "status": "success"},
    ],
)
async def test_callback_rejects_unknown_state(
    db: Any, test_dsn: str, clock: FrozenClock, params: dict[str, str]
) -> None:
    fake = FakeKiteHTTP()
    fake.on("POST", "/session/token", TOKEN_OK)
    async with running(_settings(test_dsn), clock, fake) as (_, c):
        r = await c.get("/api/kite/callback", params=params)
        assert r.status_code == 400
        assert "Login rejected" in r.text
    assert fake.requests == []  # never even asked Kite
    assert await db.fetchval("SELECT count(*) FROM app.kite_session") == 0


async def test_callback_kite_rejection_stores_nothing(
    db: Any, test_dsn: str, clock: FrozenClock
) -> None:
    fake = FakeKiteHTTP()
    fake.on("POST", "/session/token", kite_error(403, "TokenException"))
    async with running(_settings(test_dsn), clock, fake) as (_, c):
        state = await _state(c)
        r = await c.get(
            "/api/kite/callback", params={"request_token": "t", "status": "success", "state": state}
        )
        assert r.status_code == 502
        assert (await c.get("/api/kite/status")).json()["status"] == "disconnected"
    assert await db.fetchval("SELECT count(*) FROM app.kite_session") == 0


async def test_cancelled_login(db: Any, test_dsn: str, clock: FrozenClock) -> None:
    fake = FakeKiteHTTP()
    async with running(_settings(test_dsn), clock, fake) as (_, c):
        state = await _state(c)
        r = await c.get("/api/kite/callback", params={"status": "cancelled", "state": state})
        assert r.status_code == 400
        assert "cancelled" in r.text
    assert fake.requests == []


@pytest.mark.parametrize(
    ("hour", "minute", "paused", "expected"),
    [(9, 16, False, True), (8, 30, False, False), (16, 1, False, False), (9, 16, True, False)],
)
async def test_login_only_restarts_capture_during_enabled_window(
    db: Any,
    test_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    hour: int,
    minute: int,
    paused: bool,
    expected: bool,
) -> None:
    fake = FakeKiteHTTP()
    fake.on("POST", "/session/token", TOKEN_OK)
    clock = FrozenClock(datetime(2026, 11, 19, hour, minute, tzinfo=IST))
    async with running(_settings(test_dsn), clock, fake) as (app, client):
        started: list[str] = []

        def record(name: str, work: Any) -> bool:
            started.append(name)
            return True

        monkeypatch.setattr(app.state.ingest, "start_service", record)
        if paused:
            app.state.scheduler.pause_job("ticks_capture")
        state = await _state(client)
        response = await client.get(
            "/api/kite/callback",
            params={"request_token": "request", "status": "success", "state": state},
        )
        assert response.status_code == 200
        assert started == (["ticks_capture"] if expected else [])
