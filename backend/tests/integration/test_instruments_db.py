from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.core.tradingdate import MARKET_TZ
from app.data.instruments import (
    build_universe,
    parse_instruments_csv,
    replace_instruments,
    sync_universe,
)
from app.db.migrate import migrate
from tests.conftest import build_settings
from tests.fixtures.instruments import instruments_csv

pytestmark = pytest.mark.db

T1 = datetime(2026, 11, 19, 8, 0, tzinfo=MARKET_TZ)
T2 = datetime(2026, 11, 20, 8, 0, tzinfo=MARKET_TZ)
INDICES = build_settings().universe_index_symbols


async def test_master_replace_is_atomic_snapshot(db: Any) -> None:
    await migrate(db)
    rows = parse_instruments_csv(instruments_csv())
    assert await replace_instruments(db, rows, T1) == len(rows)
    assert await replace_instruments(db, rows[:3], T2) == 3  # replace, not append
    assert await db.fetchval("SELECT count(*) FROM app.instruments") == 3
    tick = await db.fetchval(
        "SELECT tick_size FROM app.instruments WHERE tradingsymbol = 'RELIANCE'"
    )
    assert tick == 10  # paisa


async def test_universe_sync_deactivates_never_deletes(db: Any) -> None:
    await migrate(db)
    full = build_universe(parse_instruments_csv(instruments_csv()), INDICES)
    first = await sync_universe(db, full, T1)
    assert first == {"active": 9, "added": 9, "reactivated": 0, "deactivated": 0}

    without_tcs = [m for m in full if m.symbol != "NSE:TCS"]
    second = await sync_universe(db, without_tcs, T2)
    assert second == {"active": 8, "added": 0, "reactivated": 0, "deactivated": 1}
    row = await db.fetchrow("SELECT active, added_at FROM app.universe WHERE symbol = 'NSE:TCS'")
    assert row["active"] is False
    assert row["added_at"] == T1  # history of membership preserved

    third = await sync_universe(db, full, T2)
    assert third == {"active": 9, "added": 0, "reactivated": 1, "deactivated": 0}
