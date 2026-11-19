"""Auth middleware + /health, without a database (app.state is stubbed; no lifespan)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.main import create_app
from tests.conftest import TEST_TOKEN, SettingsFactory


class _Conn:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    async def fetchval(self, _sql: str) -> int:
        if self.fail:
            raise ConnectionError("db down")
        return 1


class FakePool:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    @asynccontextmanager
    async def acquire(self, timeout: float | None = None) -> AsyncIterator[_Conn]:  # noqa: ASYNC109 — mirrors asyncpg
        yield _Conn(self.fail)


class FakeScheduler:
    def __init__(self, running: bool = True) -> None:
        self.running = running


def _app(make_settings: SettingsFactory, *, db_fail: bool = False, sched: bool = True) -> FastAPI:
    app = create_app(make_settings())
    app.state.pool = FakePool(db_fail)
    app.state.scheduler = FakeScheduler(sched)

    @app.post("/_probe")
    @app.put("/_probe")
    @app.patch("/_probe")
    @app.delete("/_probe")
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_health_ok_without_token(make_settings: SettingsFactory) -> None:
    async with _client(_app(make_settings)) as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "paper_mode": True,
        "db": "ok",
        "scheduler": "running",
        "version": "0.0.1",
    }


@pytest.mark.parametrize(("db_fail", "sched"), [(True, True), (False, False)])
async def test_health_is_honest_when_degraded(
    make_settings: SettingsFactory, db_fail: bool, sched: bool
) -> None:
    async with _client(_app(make_settings, db_fail=db_fail, sched=sched)) as c:
        r = await c.get("/health")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Basic {TEST_TOKEN}"},
        {"Authorization": "Bearer"},
        {"Authorization": TEST_TOKEN},
    ],
)
async def test_mutating_routes_reject_missing_or_bad_token(
    make_settings: SettingsFactory, method: str, headers: dict[str, str]
) -> None:
    async with _client(_app(make_settings)) as c:
        r = await c.request(method, "/_probe", headers=headers)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_mutating_routes_accept_valid_token(
    make_settings: SettingsFactory, method: str
) -> None:
    async with _client(_app(make_settings)) as c:
        r = await c.request(method, "/_probe", headers={"Authorization": f"Bearer {TEST_TOKEN}"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_unauthorized_short_circuits_before_handler(make_settings: SettingsFactory) -> None:
    calls: list[Any] = []
    app = create_app(make_settings())

    @app.post("/_side_effect")
    async def side_effect() -> None:
        calls.append(1)

    async with _client(app) as c:
        r = await c.post("/_side_effect")
    assert r.status_code == 401
    assert calls == []
