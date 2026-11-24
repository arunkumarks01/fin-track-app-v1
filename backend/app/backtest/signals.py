"""Causal materialisation of the first daily-long strategy signals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.analytics.indicators import Bar, calculate
from app.analytics.parameters import IndicatorPeriods
from app.scoring.rules import BUILT_INS, Strategy, assess

FIRST_COHORT = frozenset(
    {
        "year_breakout_long",
        "ema_golden_cross_long",
        "rsi_bounce_long",
        "bollinger_squeeze_long",
        "relative_strength_long",
    }
)


@dataclass(frozen=True)
class Signal:
    strategy_id: str
    revision: int
    timestamp: int


def _cohort() -> tuple[Strategy, ...]:
    return tuple(item for item in BUILT_INS if item.id in FIRST_COHORT)


def materialise_daily_long_signals(
    bars: Sequence[Bar], benchmark_closes: Mapping[int, int], periods: IndicatorPeriods
) -> tuple[Signal, ...]:
    """Evaluate only completed daily bars; a caller must execute a signal next session."""
    curves = calculate(bars, periods)
    strategies = _cohort()
    output: list[Signal] = []
    for i, bar in enumerate(bars):
        current = {name: values[i] for name, values in curves.items()}
        current.update(
            close_paisa=bar.close,
            rsi_low=30.0,
            rsi_high=70.0,
            squeeze_width_pct=10.0,
        )
        if i >= periods.relative_strength and all(
            item.t in benchmark_closes for item in bars[i - periods.relative_strength : i + 1]
        ):
            prior = bars[i - periods.relative_strength]
            current["relative_strength_pct"] = 100 * (
                bar.close * benchmark_closes[prior.t] / (prior.close * benchmark_closes[bar.t]) - 1
            )
        else:
            current["relative_strength_pct"] = None
        low, mid, high = (
            current["bollinger_lower_paisa"],
            current["bollinger_middle_paisa"],
            current["bollinger_upper_paisa"],
        )
        current["bollinger_width_pct"] = (
            100 * (high - low) / mid if low is not None and high is not None and mid else None
        )
        previous = {} if i == 0 else {name: values[i - 1] for name, values in curves.items()}
        if i:
            prior_bar = bars[i - 1]
            previous.update(
                close_paisa=prior_bar.close,
                rsi_low=30.0,
                rsi_high=70.0,
                squeeze_width_pct=10.0,
                relative_strength_pct=None,
            )
            prior_low, prior_mid, prior_high = (
                previous["bollinger_lower_paisa"],
                previous["bollinger_middle_paisa"],
                previous["bollinger_upper_paisa"],
            )
            previous["bollinger_width_pct"] = (
                100 * (prior_high - prior_low) / prior_mid
                if prior_low is not None and prior_high is not None and prior_mid
                else None
            )
        for strategy in strategies:
            if assess(strategy, current, previous, shorts_enabled=False)["status"] == "matched":
                output.append(Signal(strategy.id, strategy.revision, bar.t))
    return tuple(output)
