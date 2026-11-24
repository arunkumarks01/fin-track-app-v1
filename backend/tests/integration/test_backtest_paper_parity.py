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


async def test_strategy_protective_exit_matches_realistic_paper_ledger(db: Any) -> None:
    """Ordinary spread/volume fixture, including nonzero slippage on both fills."""
    from app.analytics.indicators import Bar as DailyBar
    from app.backtest.engine import run_daily_long
    from app.backtest.signals import Signal
    from app.backtest.strategies import daily_long_spec
    from app.core.tradingdate import canonical_daily_ts

    await migrate(db)
    now = datetime(2026, 11, 21, 10, tzinfo=MARKET_TZ)
    days = [date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19)]
    symbol = "NSE:AAA"
    await sync_universe(db, [UniverseMember(symbol, 101, "equity")], now)
    await db.executemany(
        "INSERT INTO app.nse_archive_files(trade_date,status,fetched_at) VALUES($1,'ok',$2)",
        [(day, now) for day in days],
    )
    bars = [
        Bar(10000, 10100, 9900, 10000, 10000),
        Bar(10000, 10200, 9800, 10100, 10000),
        Bar(9000, 9300, 8900, 9100, 10000),
    ]
    await db.executemany(
        """INSERT INTO app.nse_bhav
           (symbol,trade_date,prev_close,open,high,low,close,volume)
           VALUES($1,$2,10000,$3,$4,$5,$6,$7)""",
        [
            (symbol, day, b.open, b.high, b.low, b.close, b.volume)
            for day, b in zip(days, bars, strict=True)
        ],
    )
    cfg = PaperParameters(slippage_bps=5, volume_bps=10000)
    settings = build_settings(paper=cfg)
    account = await store.create(db, "strategy parity", days[0], "realistic", now, settings)
    account_id = account["id"]
    await store.advance(db, account_id, None, now, settings)
    # Engine sets risk levels from its slipped 10005 entry, with integer floor/ceiling.
    buy = Instruction(
        symbol=symbol,
        side="buy",
        kind="market",
        quantity=10,
        risk_stop_paisa=9504,
        risk_target_paisa=11006,
    )
    await store.submit(db, account_id, uuid4(), buy, now)
    await store.advance(db, account_id, days[0], now, settings)
    await store.advance(db, account_id, days[1], now, settings)
    paper = await store.overview(db, account_id)
    daily = [
        DailyBar(
            int(canonical_daily_ts(d, MARKET_TZ).timestamp()),
            b.open,
            b.high,
            b.low,
            b.close,
            b.volume,
        )
        for d, b in zip(days, bars, strict=True)
    ]
    spec = daily_long_spec("year_breakout_long_v1")
    result = run_daily_long(
        daily,
        [Signal(spec.strategy_id, 1, daily[0].t)],
        spec,
        symbol,
        days[0],
        account["capital_paisa"],
        10,
        cfg,
    )
    assert result.quantity == 0
    assert result.cash_paisa == paper["account"]["cash_paisa"]
    assert result.fills[-1].realized_paisa == paper["account"]["realized_paisa"]
    assert [(f.price_paisa, f.fees_paisa) for f in result.fills] == [
        (f["price_paisa"], f["fees_paisa"]) for f in reversed(paper["fills"])
    ]
