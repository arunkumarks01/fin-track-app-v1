"""Regression: previous-session relative strength must survive crossover evaluation."""

from app.analytics.indicators import Bar
from app.analytics.parameters import IndicatorPeriods
from app.backtest.signals import materialise_daily_long_signals


def test_relative_strength_cross_and_causal_prefix() -> None:
    bars = [Bar(i + 1, 10000, 10000, 10000, 10000, 1000) for i in range(21)]
    bars.append(Bar(22, 11000, 11000, 11000, 11000, 1000))
    benchmark = {b.t: 10000 for b in bars}
    signals = materialise_daily_long_signals(bars, benchmark, IndicatorPeriods())
    assert any(s.strategy_id == "relative_strength_long" and s.timestamp == 22 for s in signals)
    future = [*bars, Bar(23, 5000, 5000, 5000, 5000, 1000)]
    extended = materialise_daily_long_signals(future, {**benchmark, 23: 10000}, IndicatorPeriods())
    assert signals == tuple(s for s in extended if s.timestamp <= 22)
    missing = materialise_daily_long_signals(bars, {}, IndicatorPeriods())
    assert not any(s.strategy_id == "relative_strength_long" for s in missing)
