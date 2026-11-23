from datetime import date
from fractions import Fraction
from typing import Literal

import pytest

from app.backtest.replay import Session, replay
from app.sandbox.model import Bar, Instruction, fees
from app.sandbox.parameters import PaperParameters


def order(side: Literal["buy", "sell"], quantity: int = 10) -> Instruction:
    return Instruction(symbol="NSE:AAA", side=side, kind="market", quantity=quantity)


def test_next_session_execution_uses_paper_fees_and_chronological_drawdown() -> None:
    cfg = PaperParameters(slippage_bps=0, volume_bps=10000)
    result = replay(
        [
            Session(date(2026, 1, 1), Bar(10000, 10000, 10000, 10000, 100), order("buy")),
            Session(date(2026, 1, 2), Bar(10000, 10000, 9000, 9000, 100), order("sell")),
            Session(date(2026, 1, 3), Bar(11000, 11000, 11000, 11000, 100)),
        ],
        1_000_000,
        cfg,
    )
    buy_fee = sum(fees(100_000, "buy", cfg).values())
    sell_fee = sum(fees(110_000, "sell", cfg).values())
    assert [f.day for f in result.fills] == [date(2026, 1, 2), date(2026, 1, 3)]
    assert result.fills[-1].realized_paisa == 10_000 - buy_fee - sell_fee
    assert result.cash_paisa == 1_010_000 - buy_fee - sell_fee
    assert result.quantity == 0
    assert result.max_drawdown == Fraction(10_000 + buy_fee, 1_000_000)


def test_flat_price_round_trip_loses_costs_and_final_signal_cannot_fill() -> None:
    cfg = PaperParameters(slippage_bps=0, volume_bps=10000)
    bar = Bar(10000, 10000, 10000, 10000, 100)
    r = replay(
        [
            Session(date(2026, 1, i), bar, x)
            for i, x in [(1, order("buy")), (2, order("sell")), (3, order("buy"))]
        ],
        1_000_000,
        cfg,
    )
    assert r.cash_paisa < 1_000_000
    assert len(r.fills) == 2
    assert r.expired == 0
    assert r.pending_at_end


def test_fails_closed_for_unordered_sessions_or_unsupported_order() -> None:
    bar = Bar(100, 100, 100, 100, 100)
    with pytest.raises(ValueError, match="chronological"):
        replay(
            [Session(date(2026, 1, 1), bar), Session(date(2026, 1, 1), bar)],
            10000,
            PaperParameters(),
        )
    stop = Instruction(
        symbol="NSE:AAA", side="buy", kind="stop_market", quantity=1, trigger_paisa=100
    )
    with pytest.raises(ValueError, match="Only market and limit"):
        replay([Session(date(2026, 1, 1), bar, stop)], 10000, PaperParameters())


def test_partial_volume_expires_remainder_and_unaffordable_buy_is_rejected() -> None:
    bar = Bar(10000, 10000, 10000, 10000, 10)
    cfg = PaperParameters(slippage_bps=0, volume_bps=5000)
    sessions = [
        Session(date(2026, 1, 1), bar, order("buy", 10)),
        Session(date(2026, 1, 2), bar),
    ]
    partial = replay(sessions, 1_000_000, cfg)
    assert partial.fills[0].quantity == 5
    assert partial.expired == 1
    refused = replay(sessions, 10_000, cfg)
    assert refused.fills == ()
    assert refused.cash_paisa == 10_000
    assert refused.expired == 1
