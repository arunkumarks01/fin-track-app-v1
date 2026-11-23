"""The first backtest slice shares actual replay fill, fee and cost-basis behavior."""

from datetime import date, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.backtest.replay import Session, replay
from app.core.tradingdate import MARKET_TZ
from app.data.instruments import UniverseMember, sync_universe
from app.db.migrate import migrate
from app.sandbox import store
from app.sandbox.model import Bar, Instruction
from app.sandbox.parameters import PaperParameters
from tests.conftest import build_settings

pytestmark = pytest.mark.db


async def test_close_signal_backtest_matches_paper_ledger(db: Any) -> None:
    await migrate(db)
    now = datetime(2026, 11, 21, 10, tzinfo=MARKET_TZ)
    days = [date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19)]
    symbol = "NSE:AAA"
    await sync_universe(db, [UniverseMember(symbol, 101, "equity")], now)
    await db.executemany(
        "INSERT INTO app.nse_archive_files (trade_date,status,pd_rows,fetched_at) "
        "VALUES ($1,'ok',1,$2)",
        [(d, now) for d in days],
    )
    bars = [
        Bar(10000, 10000, 10000, 10000, 100),
        Bar(10000, 10000, 9000, 9000, 100),
        Bar(11000, 11000, 11000, 11000, 100),
    ]
    await db.executemany(
        """INSERT INTO app.nse_bhav
           (symbol,trade_date,prev_close,open,high,low,close,volume)
           VALUES ($1,$2,10000,$3,$4,$5,$6,$7)""",
        [
            (symbol, d, b.open, b.high, b.low, b.close, b.volume)
            for d, b in zip(days, bars, strict=True)
        ],
    )
    model = PaperParameters(slippage_bps=0, volume_bps=10000)
    settings = build_settings(paper=model)
    account = await store.create(db, "parity fixture", days[0], "learning", now, settings)
    account_id = account["id"]
    buy = Instruction(symbol=symbol, side="buy", kind="market", quantity=10)
    sell = Instruction(symbol=symbol, side="sell", kind="market", quantity=10)
    await store.advance(db, account_id, None, now, settings)
    await store.submit(db, account_id, uuid4(), buy, now)
    await store.advance(db, account_id, days[0], now, settings)
    await store.submit(db, account_id, uuid4(), sell, now)
    await store.advance(db, account_id, days[1], now, settings)
    paper = await store.overview(db, account_id)
    result = replay(
        [
            Session(days[0], bars[0], buy),
            Session(days[1], bars[1], sell),
            Session(days[2], bars[2]),
        ],
        account["capital_paisa"],
        model,
    )
    assert result.cash_paisa == paper["account"]["cash_paisa"]
    assert result.quantity == 0
    assert result.fills[-1].realized_paisa == paper["account"]["realized_paisa"]
    assert [(f.price_paisa, f.fees_paisa) for f in result.fills] == [
        (f["price_paisa"], f["fees_paisa"]) for f in reversed(paper["fills"])
    ]
