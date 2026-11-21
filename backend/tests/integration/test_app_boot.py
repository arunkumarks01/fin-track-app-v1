"""The real app boots end-to-end against TimescaleDB (Phase-0 gate: 'app boots')."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.data.jobs import DAILY_INGEST_JOB_ID, INTRADAY_INGEST_JOB_ID
from app.db.migrate import discover
from app.db.retention import CLEANUP_JOB_ID
from app.main import create_app
from tests.conftest import TEST_TOKEN

pytestmark = pytest.mark.db


async def test_boot_migrates_serves_and_restarts_cleanly(db: Any, db_settings: Settings) -> None:
    for _boot in range(2):  # second boot must be a no-op migration
        app = create_app(db_settings)
        async with app.router.lifespan_context(app):
            assert app.state.scheduler.running
            assert sorted(j.id for j in app.state.scheduler.get_jobs()) == sorted(
                [CLEANUP_JOB_ID, DAILY_INGEST_JOB_ID, INTRADAY_INGEST_JOB_ID, "news_fetch"]
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                health = await client.get("/health")
                assert health.status_code == 200
                assert health.json()["db"] == "ok"
                assert (await client.post("/health")).status_code == 401
                authed = await client.post(
                    "/health", headers={"Authorization": f"Bearer {TEST_TOKEN}"}
                )
                assert authed.status_code == 405  # auth passed; route is GET-only
        assert not app.state.scheduler.running
    assert await db.fetchval("SELECT count(*) FROM app.applied_migrations") == len(discover())
