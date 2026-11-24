"""The historical-study ledger keeps inputs and causal replay results together."""

import json
from datetime import datetime
from typing import Any

import pytest

from app.backtest.metrics import performance
from app.backtest.replay import Session, replay
from app.backtest.store import create_run, save_result
from app.backtest.strategies import daily_long_spec
from app.core.tradingdate import MARKET_TZ
from app.data.instruments import UniverseMember, sync_universe
from app.db.migrate import migrate
from app.sandbox.model import Bar, Instruction
from app.sandbox.parameters import PaperParameters

pytestmark = pytest.mark.db


async def test_persists_replay_inputs_fills_equity_and_metrics(db: Any) -> None:
    await migrate(db)
    now = datetime(2026, 11, 24, 9, tzinfo=MARKET_TZ)
    symbol = "NSE:AAA"
    await sync_universe(db, [UniverseMember(symbol, 1, "equity")], now)
    spec = daily_long_spec("rsi_bounce_long_v1")
    buy = Instruction(symbol=symbol, side="buy", kind="market", quantity=10)
    result = replay(
        [
            Session(now.date(), Bar(10000, 10000, 10000, 10000, 100), buy),
            Session(now.date().replace(day=25), Bar(10100, 10100, 10100, 10100, 100)),
        ],
        1_000_000,
        PaperParameters(slippage_bps=0, volume_bps=10_000),
    )
    run_id = await create_run(
        db,
        strategy_id=spec.strategy_id,
        revision=spec.revision,
        symbol=symbol,
        start_day=now.date(),
        end_day=now.date().replace(day=25),
        capital_paisa=1_000_000,
        assumptions={"spec": spec.id, "slippage_bps": 0},
        now=now,
    )
    await save_result(db, run_id, result, performance(result, 1_000_000))
    run = await db.fetchrow("SELECT status,assumptions FROM app.backtest_runs WHERE id=$1", run_id)
    assert run["status"] == "completed"
    assert json.loads(run["assumptions"])["spec"] == spec.id
    equity_rows = await db.fetchval(
        "SELECT count(*) FROM app.backtest_equity WHERE run_id=$1", run_id
    )
    fill_rows = await db.fetchval("SELECT count(*) FROM app.backtest_fills WHERE run_id=$1", run_id)
    ending = await db.fetchval(
        "SELECT ending_equity_paisa FROM app.backtest_metrics WHERE run_id=$1", run_id
    )
    assert equity_rows == 2
    assert fill_rows == 1
    assert ending == result.equity_curve[-1]
