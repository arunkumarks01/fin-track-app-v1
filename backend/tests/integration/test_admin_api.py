"""Admin overview: scheduled jobs, run history, quarantine/flag breakdown, cursor errors."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx
import pytest

from app.core.tradingdate import MARKET_TZ
from app.data.participant_review import retain_participant_source
from app.db.migrate import migrate
from app.main import create_app
from tests.conftest import TEST_TOKEN, build_settings
from tests.fixtures.clock import FrozenClock
from tests.unit.test_nse_reference import VOL_2015

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 18, 18, 0, tzinfo=MARKET_TZ)
TS = datetime(2026, 11, 17, 0, 0, tzinfo=MARKET_TZ)


async def test_participant_review_reports_exact_quarantined_values(db: Any, test_dsn: str) -> None:
    from datetime import date

    await migrate(db)
    day = date(2015, 1, 5)
    blob = VOL_2015.replace("290814,253340", "290814.3333,253340", 1).encode()
    await db.execute(
        "INSERT INTO app.reference_files(dataset,trade_date,status,detail,fetched_at) "
        "VALUES ('participant_vol',$1,'error','fractional',$2)",
        day,
        NOW,
    )
    await retain_participant_source(
        db, "participant_vol", day, "https://example.test/source.csv", blob, "fractional", NOW
    )
    app = create_app(build_settings(database_url=test_dsn), clock=FrozenClock(NOW))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        response = await c.get("/api/admin/participant-review")
    assert response.status_code == 200
    [item] = response.json()["files"]
    assert item["status"] == "quarantined"
    assert item["analysis"]["date_status"] == "verified"
    assert item["analysis"]["rows"]["Client"]["fut_idx_long"] == "290814.3333"
    assert item["analysis"]["source_total_differences"]["Client.long"] == "-0.3333"


async def test_configured_services_are_scheduled_and_require_a_kite_session(
    db: Any, test_dsn: str
) -> None:
    await migrate(db)
    settings = build_settings(
        database_url=test_dsn,
        kite_api_key="test-key-123",
        kite_api_secret="test-secret-123",
        scoring_scan_enabled=True,
    )
    app = create_app(settings, clock=FrozenClock(datetime(2026, 11, 23, 10, tzinfo=MARKET_TZ)))
    async with app.router.lifespan_context(app):
        jobs = {job.id for job in app.state.scheduler.get_jobs()}
        assert {
            "ticks_capture",
            "option_chain",
            "scoring_swing_morning",
            "scoring_swing_close",
            "scoring_intraday",
        } <= jobs
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for name in ("ticks_capture", "option_chain"):
                response = await client.post(
                    f"/api/admin/jobs/{name}/start",
                    headers={"Authorization": f"Bearer {TEST_TOKEN}"},
                )
                assert response.status_code == 409
                assert "session" in response.json()["detail"]


async def test_overview_reports_jobs_quarantine_flags_and_cursor_errors(
    db: Any, test_dsn: str
) -> None:
    await migrate(db)
    await db.executemany(
        """INSERT INTO app.ohlcv_quarantine
           (symbol, timeframe, ts, reason, detail, raw, source, quarantined_at)
           VALUES ($1, $2, $3, $4, 'x', '[]', 'test', $5)""",
        [
            ("NSE:AAA", "1d", TS, "ohlc_order", NOW),
            ("NSE:BBB", "1d", TS, "ohlc_order", NOW),
            ("NSE:AAA", "1d", TS, "non_positive_price", NOW),
            ("NSE:AAA", "1m", TS, "out_of_range", NOW),
        ],
    )
    await db.execute(
        """INSERT INTO app.ohlcv_flags (symbol, timeframe, ts, flag, detail, flagged_at)
           VALUES ('NSE:AAA', '1d', $1, 'large_gap', 'x', $2)""",
        TS,
        NOW,
    )
    await db.execute(
        """INSERT INTO app.ingest_cursor
           (symbol, timeframe, last_ts, status, last_error, updated_at)
           VALUES ('NSE:AAA', '1d', NULL, 'error', 'Kite 500', $1),
                  ('NSE:BBB', '1d', $2, 'ok', NULL, $1)""",
        NOW,
        TS,
    )
    sid = await db.fetchval(
        "INSERT INTO app.instrument_ids (symbol) VALUES ('NSE:CCC') RETURNING sid"
    )
    await db.execute(
        """INSERT INTO app.intraday_cursor (sid, status, last_error, updated_at)
           VALUES ($1, 'error', 'timeout', $2)""",
        sid,
        NOW,
    )

    app = create_app(build_settings(database_url=test_dsn), clock=FrozenClock(NOW))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/api/admin/overview")  # read-only: no token needed
    assert r.status_code == 200
    body = r.json()
    assert datetime.fromisoformat(body["history_since"]) == NOW
    assert body["job_running"] is False
    assert body["runs"] == []  # in-memory history starts empty on boot (said honestly)
    assert {"ticks_capture", "option_chain", "scoring_swing", "scoring_intraday"} <= set(
        body["operable_jobs"]
    )
    sched = body["scheduler"]
    assert sched["running"] is True
    assert [j["id"] for j in sched["jobs"]] == [
        "daily_ingest",
        "intraday_ingest",
        "news_fetch",
        "ticks_retention_cleanup",
    ]
    assert all(j["next_run_at"] and j["trigger"].startswith("cron") for j in sched["jobs"])
    assert body["quarantine"] == [
        {"timeframe": "1d", "reason": "ohlc_order", "count": 2},
        {"timeframe": "1d", "reason": "non_positive_price", "count": 1},
        {"timeframe": "1m", "reason": "out_of_range", "count": 1},
    ]
    assert body["flags"] == [{"timeframe": "1d", "flag": "large_gap", "count": 1}]
    assert sorted((e["grain"], e["symbol"], e["error"]) for e in body["cursor_errors"]) == [
        ("1d", "NSE:AAA", "Kite 500"),
        ("1m", "NSE:CCC", "timeout"),
    ]


async def test_controls_only_stop_the_selected_run_and_pause_future_work(
    test_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(build_settings(database_url=test_dsn), clock=FrozenClock(NOW))
    auth = {"Authorization": f"Bearer {TEST_TOKEN}"}
    entered, cleaned_up = asyncio.Event(), asyncio.Event()

    async def backfill(*args: Any) -> Any:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    async with app.router.lifespan_context(app):
        runner = app.state.ingest
        monkeypatch.setattr(runner, "_reference", backfill)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            ref = "/api/admin/jobs/reference_ingest"
            schedule = "/api/admin/jobs/news_fetch"
            assert (await c.post(f"{ref}/start")).status_code == 401
            assert (await c.post(f"{ref}/start", headers=auth)).status_code == 202
            await asyncio.wait_for(entered.wait(), timeout=1)
            assert (await c.post(f"{ref}/start", headers=auth)).status_code == 409
            assert (await c.post(f"{ref}/stop", headers=auth)).status_code == 422
            assert (
                await c.post(f"{ref}/stop", headers=auth, params={"started_at": TS.isoformat()})
            ).status_code == 409
            assert not cleaned_up.is_set()
            assert (
                await c.post(f"{ref}/stop", headers=auth, params={"started_at": NOW.isoformat()})
            ).status_code == 202
            await asyncio.wait_for(runner.wait(), timeout=1)
            assert cleaned_up.is_set()
            assert (await c.post(f"{schedule}/pause", headers=auth)).status_code == 200
            overview = (await c.get("/api/admin/overview")).json()
            assert overview["runs"][0]["status"] == "stopped"
            assert overview["job_running"] is False
            assert (
                next(
                    job["next_run_at"]
                    for job in overview["scheduler"]["jobs"]
                    if job["id"] == "news_fetch"
                )
                is None
            )
            assert (await c.post(f"{schedule}/resume", headers=auth)).status_code == 200
            assert app.state.scheduler.get_job("news_fetch").next_run_time is not None
