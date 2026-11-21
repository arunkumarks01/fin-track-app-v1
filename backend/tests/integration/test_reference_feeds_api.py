"""Phase 1e feed ingestion and reviewed CSV import through the public API."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.main import create_app
from tests.conftest import TEST_TOKEN, build_settings
from tests.fixtures.clock import FrozenClock
from tests.unit.test_news_fred_calendar import CAL_OK, RSS

pytestmark = pytest.mark.db
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
NOW = datetime(2026, 11, 21, 4, 0, tzinfo=UTC)


@asynccontextmanager
async def _client(
    test_dsn: str,
    clock: FrozenClock,
    handler: Callable[[httpx.Request], httpx.Response],
    **overrides: Any,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        build_settings(database_url=test_dsn, **overrides),
        clock=clock,
        http_transport=httpx.MockTransport(handler),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        yield client


async def test_news_respects_feed_ttl_and_refusal_and_deduplicates(db: Any, test_dsn: str) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/refused":
            return httpx.Response(403)
        return httpx.Response(200, content=RSS)

    clock = FrozenClock(NOW)
    async with _client(
        test_dsn,
        clock,
        handler,
        news_feeds={
            "Markets": "https://feeds.example/rss",
            "Other": "https://feeds.example/refused",
        },
    ) as c:
        first = await c.post("/api/data/ingest/news", headers=AUTH)
        assert first.status_code == 200
        assert first.json()["result"]["new_items"] == 2
        await c.post("/api/data/ingest/news", headers=AUTH)
        assert seen == ["/rss", "/refused"]
        clock.advance(15 * 60)
        third = await c.post("/api/data/ingest/news", headers=AUTH)
        assert third.json()["result"]["new_items"] == 0
        assert seen == ["/rss", "/refused", "/rss"]

        news = (await c.get("/api/data/news")).json()
        assert {f["source"]: f["status"] for f in news["feeds"]} == {
            "Markets": "ok",
            "Other": "refused",
        }
        assert len(news["items"]) == 2
        headline = next(i for i in news["items"] if i["link"] == "https://example.com/a")
        assert headline["summary"] == "Stocks rose today."


async def test_fred_reads_latest_two_and_requests_only_new_dates(db: Any, test_dsn: str) -> None:
    starts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(request.url.params["observation_start"])
        observations = (
            [
                {"date": "2026-11-18", "value": "5970"},
                {"date": "2026-11-19", "value": "5974.07"},
                {"date": "2026-11-20", "value": "."},
            ]
            if len(starts) == 1
            else []
        )
        return httpx.Response(200, json={"observations": observations})

    clock = FrozenClock(NOW)
    async with _client(test_dsn, clock, handler) as c:
        assert (await c.post("/api/data/ingest/fred", headers=AUTH)).status_code == 503
        assert (await c.get("/api/data/macro")).json() == {"key_configured": False, "series": []}
    assert starts == []

    async with _client(
        test_dsn,
        clock,
        handler,
        fred_api_key="mock-key",
        fred_series={"SP500": ("S&P 500", "index points", 2)},
    ) as c:
        first = await c.post("/api/data/ingest/fred", headers=AUTH)
        assert first.status_code == 200
        assert first.json()["result"]["stored"] == 2
        second = await c.post("/api/data/ingest/fred", headers=AUTH)
        assert second.json()["result"]["stored"] == 0
        assert starts == ["2015-01-01", "2026-11-20"]
        macro = (await c.get("/api/data/macro")).json()
        assert macro["key_configured"] is True
        [series] = macro["series"]
        assert series["observations"] == [
            {"date": "2026-11-19", "value_scaled": 597407},
            {"date": "2026-11-18", "value_scaled": 597000},
        ]


async def test_calendar_import_is_authenticated_atomic_and_idempotent(
    db: Any, test_dsn: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected outbound request: {request.url}")

    async with _client(test_dsn, FrozenClock(NOW), handler) as c:
        path = "/api/data/calendar/import"
        assert (await c.post(path, content=CAL_OK)).status_code == 401
        for bad in (b"\xff", (CAL_OK + "bad,row\n").encode(), (CAL_OK + '"unclosed\n').encode()):
            response = await c.post(path, content=bad, headers=AUTH)
            assert response.status_code == 422
            assert response.json()["imported"] == 0
        assert (await c.get("/api/data/calendar")).json()["events"] == []
        created = await c.post(path, content=CAL_OK, headers=AUTH)
        assert created.json() == {"rows": 2, "imported": 2}
        repeated = await c.post(path, content=CAL_OK, headers=AUTH)
        assert repeated.json() == {"rows": 2, "imported": 0}
        india, us = (await c.get("/api/data/calendar")).json()["events"]
        assert india["time"] == "10:00"
        assert us["time"] is None
        assert us["source_url"] == "https://www.federalreserve.gov/y"
