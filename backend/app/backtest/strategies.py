"""Versioned, executable assumptions for the first historical strategy studies.

These are research baselines, not recommendations.  A signal from ``scoring.rules`` becomes
eligible at a final daily close; the order is modelled at the next session open.  Every
assumption is surfaced in the spec so results cannot hide a discretionary exit rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class DailyLongSpec:
    id: str
    strategy_id: str
    revision: int
    entry: Literal["next_session_market"]
    stop_loss_bps: int
    target_bps: int
    max_holding_sessions: int
    one_position_per_symbol: bool = True
    # Explicit opt-in; existing v1 studies retain their original fixed-stop assumptions.
    trailing_stop_bps: int = 0


# A common, deliberately conservative baseline lets the first comparison isolate each
# signal. These inputs are research parameters and must be recorded with every run.
DAILY_LONG_BASELINES: tuple[DailyLongSpec, ...] = (
    DailyLongSpec(
        "year_breakout_long_v1", "year_breakout_long", 1, "next_session_market", 500, 1000, 20
    ),
    DailyLongSpec(
        "ema_golden_cross_long_v1", "ema_golden_cross_long", 1, "next_session_market", 500, 1000, 20
    ),
    DailyLongSpec("rsi_bounce_long_v1", "rsi_bounce_long", 1, "next_session_market", 400, 800, 15),
    DailyLongSpec(
        "bollinger_squeeze_long_v1",
        "bollinger_squeeze_long",
        1,
        "next_session_market",
        500,
        1000,
        15,
    ),
    DailyLongSpec(
        "relative_strength_long_v1",
        "relative_strength_long",
        1,
        "next_session_market",
        500,
        1000,
        20,
    ),
)


def daily_long_spec(identifier: str) -> DailyLongSpec:
    """Return one enabled baseline or fail closed for an unknown strategy revision."""
    for spec in DAILY_LONG_BASELINES:
        if spec.id == identifier:
            return spec
    raise ValueError(f"Unknown daily-long backtest strategy: {identifier}")
