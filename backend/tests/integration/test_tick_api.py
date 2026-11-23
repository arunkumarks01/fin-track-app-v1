"""Slice 1d read endpoints: capture status, export ledger, latest chain snapshot."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from app.db.migrate import migrate
from app.main import create_app
from tests.conftest import build_settings
from tests.fixtures.clock import FrozenClock

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 23, 5, 0, tzinfo=UTC)


async def _client(db: Any, test_dsn: str) -> Any:
    await migrate(db)
    app = create_app(build_settings(database_url=test_dsn), clock=FrozenClock(NOW))
    return app


async def test_ticks_status_and_latest_chain(db: Any, test_dsn: str) -> None:
    app = await _client(db, test_dsn)
    await db.execute(
        """INSERT INTO app.tick_capture_runs (session_date, started_at, ended_at, status,
               instruments, ticks, rejected, reconnects, detail)
           VALUES ('2026-11-20', $1, $1, 'ok', 226, 5000000, 3, 2, NULL)""",
        NOW,
    )
    await db.execute(
        """INSERT INTO app.tick_exports (chunk_start, chunk_end, status, rows, bytes, sha256, path,
               exported_at, dropped_at)
           VALUES ('2026-11-10', '2026-11-11', 'verified', 5000000, 900000000, 'ab', '/x', $1, $1),
                  ('2026-11-11', '2026-11-12', 'mismatch', 0, 0, NULL, NULL, $1, NULL)""",
        NOW,
    )
    ts = datetime(2026, 11, 23, 4, 35, tzinfo=UTC)
    await db.execute(
        """INSERT INTO app.option_snapshot_meta (snapshot_ts, underlying, spot_paisa, fut_paisa,
               fut_expiry, contracts, status)
           VALUES ($1, 'NIFTY', 2551230, 2553000, $2, 2, 'ok')""",
        ts,
        date(2026, 11, 26),
    )
    await db.executemany(
        """INSERT INTO app.option_snapshots (snapshot_ts, underlying, instrument_token, expiry,
               strike_paisa, opt_type, ltp, volume, oi, bid, bid_qty, ask, ask_qty)
           VALUES ($1, 'NIFTY', $2, $3, 2550000, $4, 10005, 10, 500, 10000, 75, 10010, 75)""",
        [(ts, 1, date(2026, 11, 26), "CE"), (ts, 2, date(2026, 11, 26), "PE")],
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        t = (await c.get("/api/data/ticks")).json()
        assert t["capture"] is None  # Kite isn't configured here: said honestly
        assert t["runs"][0]["ticks"] == 5_000_000
        assert (t["exports"]["verified"], t["exports"]["dropped"], t["exports"]["mismatch"]) == (
            1,
            1,
            1,
        )
        assert t["exports"]["bytes"] == 900_000_000
        assert t["chain"]["latest"][0]["underlying"] == "NIFTY"
        assert t["hot"]["oldest"] is None  # no ticks stored

        ch = (await c.get("/api/data/chain", params={"underlying": "nifty"})).json()
        assert (ch["spot_paisa"], ch["fut_expiry"], ch["status"]) == (2551230, "2026-11-26", "ok")
        assert [(r["type"], r["strike_paisa"], r["bid"]) for r in ch["rows"]] == [
            ("CE", 2550000, 10000),
            ("PE", 2550000, 10000),
        ]
        assert (await c.get("/api/data/chain", params={"underlying": "NOPE"})).status_code == 404
