"""Versioned strategy definitions and the evaluator shared by both pipelines."""

from __future__ import annotations

import operator
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

Mode = Literal["swing", "intraday"]
Direction = Literal["long", "short"]
Value = int | float | None
Instrument = Literal["underlying", "future", "option"]
Context = Mapping[str, Value]
Comparator = Literal[">", ">=", "<", "<="]

_COMPARE = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}
_REVERSE: dict[Comparator, Comparator] = {">": "<=", ">=": "<", "<": ">=", "<=": ">"}


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=140)
    observed: str = Field(pattern=r"^[a-z0-9_]{1,75}$")
    comparison: Comparator
    threshold: str | FiniteFloat
    previous: bool = False


class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=80)
    mode: Mode
    direction: Direction
    rules: tuple[Condition, ...] = Field(min_length=1, max_length=20)
    invalidation: str | None = Field(default=None, max_length=250)
    revision: int = Field(default=1, ge=1)


@dataclass(frozen=True)
class Evidence:
    label: str
    field: str
    comparison: str
    observed: Value
    threshold: Value
    threshold_field: str | None
    result: Literal["pass", "fail", "unavailable"]
    previous: bool


def rule(
    label: str,
    observed: str,
    comparison: Comparator,
    threshold: str | float,
    previous: bool = False,
) -> Condition:
    return Condition(
        label=label,
        observed=observed,
        comparison=comparison,
        threshold=threshold,
        previous=previous,
    )


def cross(observed: str, comparison: Comparator, threshold: str | float) -> tuple[Condition, ...]:
    return (
        rule(
            "Previous candle was on the other side", observed, _REVERSE[comparison], threshold, True
        ),
        rule("Latest candle crossed the threshold", observed, comparison, threshold),
    )


def assess(
    strategy: Strategy, current: Context, previous: Context, shorts_enabled: bool
) -> dict[str, Any]:
    evidence = []
    for condition in strategy.rules:
        row = previous if condition.previous else current
        observed = row.get(condition.observed)
        configured = condition.threshold
        field = configured if isinstance(configured, str) else None
        threshold = row.get(configured) if isinstance(configured, str) else configured
        outcome: Literal["pass", "fail", "unavailable"]
        if observed is None or threshold is None:
            outcome = "unavailable"
        else:
            outcome = "pass" if _COMPARE[condition.comparison](observed, threshold) else "fail"
        evidence.append(
            Evidence(
                condition.label,
                condition.observed,
                condition.comparison,
                observed,
                threshold,
                field,
                outcome,
                condition.previous,
            )
        )
    status = "matched"
    if strategy.direction == "short" and not shorts_enabled:
        status = "disabled"
    elif any(row.result == "unavailable" for row in evidence):
        status = "unavailable"
    elif any(row.result == "fail" for row in evidence):
        status = "no_match"
    return {
        "id": strategy.id,
        "name": strategy.name,
        "revision": strategy.revision,
        "direction": strategy.direction,
        "status": status,
        "invalidation": strategy.invalidation,
        "evidence": [asdict(row) for row in evidence],
    }


def catalogue() -> tuple[Strategy, ...]:
    strategies: list[Strategy] = []
    directions: tuple[Direction, ...] = ("long", "short")
    for direction in directions:
        up: Comparator = ">" if direction == "long" else "<"
        down: Comparator = "<" if direction == "long" else ">"
        side = "high" if direction == "long" else "low"
        band = "upper" if direction == "long" else "lower"
        rsi = "rsi_low" if direction == "long" else "rsi_high"

        def add(
            identifier: str,
            name: str,
            mode: Mode,
            rules: Sequence[Condition],
            invalidation: str,
            side_direction: Direction = direction,
        ) -> None:
            strategies.append(
                Strategy(
                    id=f"{identifier}_{side_direction}",
                    name=name,
                    mode=mode,
                    direction=side_direction,
                    rules=tuple(rules),
                    invalidation=invalidation,
                )
            )

        add(
            "year_breakout",
            "52-week breakout",
            "swing",
            (
                rule(
                    "Close beyond the preceding 252 sessions",
                    "close_paisa",
                    up,
                    f"prior_252_{side}_paisa",
                ),
            ),
            "A daily close back inside the prior 252-session range.",
        )
        add(
            "ema_golden_cross",
            "EMA 50/200 cross",
            "swing",
            cross("ema_50_paisa", up, "ema_200_paisa"),
            "EMA 50 crosses back through EMA 200.",
        )
        add(
            "rsi_bounce",
            "RSI bounce",
            "swing",
            cross("rsi_pct", up, rsi),
            "RSI crosses back through the bounce threshold.",
        )
        add(
            "bollinger_squeeze",
            "Daily Bollinger squeeze breakout",
            "swing",
            (
                rule(
                    "Prior band width as a percentage of the midpoint",
                    "bollinger_width_pct",
                    "<=",
                    "squeeze_width_pct",
                    True,
                ),
                *cross("close_paisa", up, f"bollinger_{band}_paisa"),
            ),
            "A daily close back inside the Bollinger bands.",
        )
        add(
            "relative_strength",
            "Relative strength vs NIFTY",
            "swing",
            cross("relative_strength_pct", up, 0),
            "Relative strength crosses back through zero.",
        )

        add(
            "opening_range",
            "Opening-range breakout",
            "intraday",
            (
                rule(
                    "Previous close at or inside the opening range",
                    "close_paisa",
                    _REVERSE[up],
                    f"opening_range_{side}_paisa",
                    True,
                ),
                rule(
                    "Close beyond the completed opening range",
                    "close_paisa",
                    up,
                    f"opening_range_{side}_paisa",
                ),
            ),
            "A completed 5-minute candle closes back inside the opening range.",
        )
        add(
            "vwap",
            "VWAP reclaim" if direction == "long" else "VWAP rejection",
            "intraday",
            cross("close_paisa", up, "vwap_paisa"),
            "Price closes back across session VWAP.",
        )
        add(
            "gap_fill",
            "Gap fill",
            "intraday",
            (
                rule(
                    "Opening gap favors a move toward the preceding close",
                    "gap_pct",
                    down,
                    "negative_gap_min_pct" if direction == "long" else "gap_min_pct",
                ),
                rule("Price turns toward the prior close", "close_change_paisa", up, 0),
                rule(
                    "Prior close has yet to be reached", "close_paisa", down, "previous_close_paisa"
                ),
            ),
            "Price reverses against the prior-close target.",
        )
        add(
            "momentum",
            "Momentum continuation",
            "intraday",
            (
                *cross("close_paisa", up, f"prior_20_{side}_paisa"),
                rule(
                    "Volume relative to its prior average",
                    "relative_volume",
                    ">=",
                    "momentum_min_relative_volume",
                ),
            ),
            "Price closes back inside the preceding 20-bar range.",
        )
        add(
            "cpr_breakout",
            "Central pivot range breakout",
            "intraday",
            cross(
                "close_paisa", up, "cpr_top_paisa" if direction == "long" else "cpr_bottom_paisa"
            ),
            "Price closes back inside the central pivot range.",
        )
        add(
            "bollinger_keltner",
            "Bollinger-Keltner squeeze breakout",
            "intraday",
            (
                rule(
                    "Prior upper Bollinger band inside Keltner",
                    "bollinger_upper_paisa",
                    "<=",
                    "keltner_upper_paisa",
                    True,
                ),
                rule(
                    "Prior lower Bollinger band inside Keltner",
                    "bollinger_lower_paisa",
                    ">=",
                    "keltner_lower_paisa",
                    True,
                ),
                *cross("close_paisa", up, f"bollinger_{band}_paisa"),
            ),
            "Price closes back inside the Bollinger bands.",
        )
        add(
            "ema_cross",
            "EMA 9/21 cross",
            "intraday",
            cross("ema_9_paisa", up, "ema_21_paisa"),
            "EMA 9 crosses back through EMA 21.",
        )
    return tuple(strategies)


BUILT_INS = catalogue()
