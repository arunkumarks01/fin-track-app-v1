from datetime import date
from fractions import Fraction
from typing import Literal

from app.backtest.metrics import performance
from app.backtest.replay import Session, replay
from app.sandbox.model import Bar, Instruction, fees
from app.sandbox.parameters import PaperParameters


def _order(side: Literal["buy", "sell"]) -> Instruction:
    return Instruction(symbol="NSE:AAA", side=side, kind="market", quantity=10)


def test_performance_uses_marked_equity_and_closed_trade_outcomes() -> None:
    config = PaperParameters(slippage_bps=0, volume_bps=10_000)
    result = replay(
        [
            Session(date(2026, 1, 1), Bar(10000, 10000, 10000, 10000, 100), _order("buy")),
            Session(date(2026, 1, 2), Bar(10000, 10000, 9000, 9000, 100), _order("sell")),
            Session(date(2026, 1, 3), Bar(11000, 11000, 11000, 11000, 100)),
        ],
        1_000_000,
        config,
    )
    summary = performance(result, 1_000_000)
    assert summary.ending_equity_paisa == result.equity_curve[-1]
    assert summary.net_paisa == 10_000 - sum(
        sum(item.values())
        for item in (
            fees(100_000, "buy", config),
            fees(110_000, "sell", config),
        )
    )
    assert (summary.closed_trades, summary.wins, summary.losses) == (1, 1, 0)
    assert summary.profit_factor is None
    assert summary.return_fraction == Fraction(summary.net_paisa, 1_000_000)


def test_performance_does_not_claim_profit_factor_without_losses() -> None:
    result = replay(
        [Session(date(2026, 1, 1), Bar(100, 100, 100, 100, 10))],
        10_000,
        PaperParameters(),
    )
    summary = performance(result, 10_000)
    assert summary.profit_factor is None
    assert summary.max_drawdown == Fraction(0)
