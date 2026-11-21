"""Admin overview: scheduled jobs, run history, quarantine/flag breakdown, cursor errors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import pytest

from app.core.tradingdate import MARKET_TZ
from app.db.migrate import migrate
from app.main import create_app
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 18, 18, 0, tzinfo=MARKET_TZ)
TS = datetime(2026, 11, 17, 0, 0, tzinfo=MARKET_TZ)


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
