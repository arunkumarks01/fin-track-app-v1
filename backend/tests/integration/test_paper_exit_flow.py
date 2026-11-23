"""Database integration checks for next-session automatic paper exits."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.core.tradingdate import MARKET_TZ
from app.data.instruments import UniverseMember, sync_universe
from app.db.migrate import migrate
from app.sandbox import store
from app.sandbox.model import Instruction
from tests.conftest import build_settings

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 21, 10, tzinfo=MARKET_TZ)
FIRST = date(2026, 11, 18)
SECOND = date(2026, 11, 19)
THIRD = date(2026, 11, 20)


async def test_realistic_stop_exit_uses_next_session_bar_without_lookahead(
    db: Any, test_dsn: str
) -> None:
    await migrate(db)
    symbol = "NSE:AAA"
    await sync_universe(db, [UniverseMember(symbol, 101, "equity")], NOW)
    await db.executemany(
        """INSERT INTO app.nse_archive_files(trade_date,status,pd_rows,fetched_at)
           VALUES($1,'ok',1,$2)""",
        [(FIRST, NOW), (SECOND, NOW)],
    )
    await db.executemany(
        """INSERT INTO app.nse_bhav
           (symbol,trade_date,prev_close,open,high,low,close,volume)
           VALUES($1,$2,10000,$3,$4,$5,$6,100000)""",
        [(symbol, FIRST, 10000, 10500, 9500, 10200), (symbol, SECOND, 9000, 9200, 8000, 8500)],
    )
    settings = build_settings(database_url=test_dsn)
    account = await store.create(db, "Protected", FIRST, "realistic", NOW, settings)
    order = await store.submit(
        db,
        account["id"],
        uuid4(),
        Instruction(
            symbol=symbol,
            side="buy",
            kind="market",
            quantity=2,
            risk_stop_paisa=9000,
            risk_target_paisa=12000,
        ),
        NOW,
    )
    assert (await store.advance(db, account["id"], None, NOW, settings))["fills"] == 1
    assert (await store.overview(db, account["id"]))["positions"][0]["quantity"] == 2

    assert (await store.advance(db, account["id"], FIRST, NOW, settings))["fills"] == 1
    result = await store.overview(db, account["id"])
    assert result["positions"] == []
    parent = await db.fetchrow(
        "SELECT exit_order_id,exit_reason FROM app.paper_orders WHERE id=$1", order["id"]
    )
    assert parent["exit_order_id"] is not None
    assert parent["exit_reason"] == "stop_hit"
    exit_fill = await db.fetchrow(
        """SELECT f.trade_day,f.price_paisa,o.side,o.status FROM app.paper_fills f
           JOIN app.paper_orders o ON o.id=f.order_id WHERE o.id=$1""",
        parent["exit_order_id"],
    )
    assert (
        exit_fill["trade_day"],
        exit_fill["price_paisa"],
        exit_fill["side"],
        exit_fill["status"],
    ) == (
        SECOND,
        8995,
        "sell",
        "filled",
    )


async def test_trailing_stop_tightens_after_a_finalized_bar_then_protects_next_day(
    db: Any, test_dsn: str
) -> None:
    await migrate(db)
    symbol = "NSE:AAA"
    await sync_universe(db, [UniverseMember(symbol, 101, "equity")], NOW)
    await db.executemany(
        """INSERT INTO app.nse_archive_files(trade_date,status,pd_rows,fetched_at)
           VALUES($1,'ok',1,$2)""",
        [(FIRST, NOW), (SECOND, NOW), (THIRD, NOW)],
    )
    await db.executemany(
        """INSERT INTO app.nse_bhav
           (symbol,trade_date,prev_close,open,high,low,close,volume)
           VALUES($1,$2,10000,$3,$4,$5,$6,100000)""",
        [
            (symbol, FIRST, 10000, 10500, 9500, 10200),
            # This bar earns a 10% trailing stop at 10,800, effective tomorrow.
            (symbol, SECOND, 10200, 12000, 10000, 10000),
            # It gaps below that earned stop on the following session.
            (symbol, THIRD, 10500, 10700, 10000, 10100),
        ],
    )
    settings = build_settings(database_url=test_dsn)
    account = await store.create(db, "Trailing", FIRST, "realistic", NOW, settings)
    order = await store.submit(
        db,
        account["id"],
        uuid4(),
        Instruction(
            symbol=symbol,
            side="buy",
            kind="market",
            quantity=2,
            risk_stop_paisa=9000,
            risk_target_paisa=13000,
            trailing_stop_bps=1000,
        ),
        NOW,
    )
    await store.advance(db, account["id"], None, NOW, settings)
    await store.advance(db, account["id"], FIRST, NOW, settings)
    trailed = await db.fetchrow(
        """SELECT highest_paisa_seen,current_stop_paisa,exit_order_id
           FROM app.paper_orders WHERE id=$1""",
        order["id"],
    )
    assert (
        trailed["highest_paisa_seen"],
        trailed["current_stop_paisa"],
        trailed["exit_order_id"],
    ) == (
        12000,
        10800,
        None,
    )

    assert (await store.advance(db, account["id"], SECOND, NOW, settings))["fills"] == 1
    parent = await db.fetchrow(
        "SELECT exit_order_id,exit_reason FROM app.paper_orders WHERE id=$1", order["id"]
    )
    assert parent["exit_reason"] == "stop_hit"
    assert parent["exit_order_id"] is not None
