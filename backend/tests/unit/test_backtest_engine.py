"""Hand-computed causal execution and cost fixtures, without any external services."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.analytics.indicators import Bar
from app.backtest.engine import run_daily_long
from app.backtest.metrics import performance
from app.backtest.replay import Replay
from app.backtest.signals import Signal
from app.backtest.strategies import DailyLongSpec, daily_long_spec
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts
from app.sandbox.model import fees
from app.sandbox.parameters import PaperParameters

START = date(2026, 1, 1)
SPEC = daily_long_spec("year_breakout_long_v1")
MODEL = PaperParameters(slippage_bps=0, volume_bps=10000)


def bar(
    i: int, o: int = 10000, h: int = 10000, low: int = 10000, c: int = 10000, volume: int = 1000
) -> Bar:
    return Bar(
        int(canonical_daily_ts(START + timedelta(days=i), MARKET_TZ).timestamp()),
        o,
        h,
        low,
        c,
        volume,
    )


def run(
    bars: list[Bar],
    *,
    spec: DailyLongSpec = SPEC,
    model: PaperParameters = MODEL,
) -> Replay:
    return run_daily_long(
        bars, [Signal(SPEC.strategy_id, 1, bars[0].t)], spec, "NSE:AAA", START, 1_000_000, 10, model
    )


def test_next_open_entry_then_gap_stop_uses_paper_costs() -> None:
    result = run([bar(0), bar(1, 11000, 11200, 10900, 11000), bar(2, 10000, 10300, 9900, 10100)])
    assert [(f.day, f.side, f.price_paisa) for f in result.fills] == [
        (START + timedelta(days=1), "buy", 11000),
        (START + timedelta(days=2), "sell", 10000),
    ]
    charges = sum(fees(110000, "buy", MODEL).values()) + sum(fees(100000, "sell", MODEL).values())
    assert result.cash_paisa == 1_000_000 - 10000 - charges
    assert result.fills[-1].realized_paisa == -10000 - charges
    assert result.fills[-1].reason == "stop_hit"


def test_entry_day_double_touch_is_stop_first() -> None:
    result = run([bar(0), bar(1, 10000, 12000, 9000, 11000)])
    assert [f.price_paisa for f in result.fills] == [10000, 9500]
    assert result.fills[-1].reason == "stop_hit"


def test_target_and_max_holding_exit_at_known_prices() -> None:
    target = run([bar(0), bar(1, 10000, 11100, 9900, 11000)])
    assert target.fills[-1].price_paisa == 11000
    assert target.fills[-1].reason == "target_hit"
    timed = run([bar(i) for i in range(5)], spec=replace(SPEC, max_holding_sessions=2))
    assert timed.fills[-1].day == START + timedelta(days=3)
    assert timed.fills[-1].reason == "max_holding"


def test_trail_earned_today_only_applies_tomorrow_and_never_loosens() -> None:
    result = run(
        [
            bar(0),
            bar(1, 10000, 10800, 9800, 10600),
            bar(2, 10400, 10500, 10300, 10400),
            bar(3, 10200, 10400, 10100, 10300),
        ],
        spec=replace(SPEC, trailing_stop_bps=500),
    )
    # 10800 * .95 = 10260. Today's low of 9800 cannot retrospectively hit that stop.
    assert len(result.fills) == 2
    assert result.fills[-1].day == START + timedelta(days=3)
    assert result.fills[-1].price_paisa == 10200


def test_final_signal_stays_pending_and_flat_roundtrip_loses_costs() -> None:
    final = run([bar(0)])
    assert not final.fills
    assert final.pending_at_end
    result = run([bar(i) for i in range(4)], spec=replace(SPEC, max_holding_sessions=1))
    assert result.cash_paisa < 1_000_000
    assert performance(result, 1_000_000).losses == 1


def test_partial_exit_latches_remaining_liquidation_and_counts_one_trade() -> None:
    result = run(
        [bar(0), bar(1), bar(2, 9400, 9500, 9300, 9400, 4), bar(3, 9600, 9600, 9600, 9600, 6)]
    )
    assert [f.quantity for f in result.fills] == [10, 4, 6]
    assert result.quantity == 0
    assert performance(result, 1_000_000).closed_trades == 1
    partial = run([bar(0), bar(1), bar(2, 9400, 9500, 9300, 9400, 4)])
    assert partial.quantity == 6
    assert performance(partial, 1_000_000).closed_trades == 0


def test_market_slippage_does_not_use_future_range_to_improve_fill() -> None:
    result = run([bar(0), bar(1)], model=PaperParameters(slippage_bps=5, volume_bps=10000))
    assert result.fills[0].price_paisa == 10005


def test_prefix_invariance_and_reject_invalid_history() -> None:
    bars = [bar(0), bar(1), bar(2, 10500, 10600, 10400, 10500)]
    short = run(bars)
    long = run([*bars, bar(3, 12000, 13000, 5000, 7000)])
    assert short.fills == long.fills[: len(short.fills)]
    assert short.equity_curve == long.equity_curve[: len(short.equity_curve)]
    with pytest.raises(ValueError, match="chronological"):
        run([bar(1), bar(0)])
