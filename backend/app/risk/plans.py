"""Build a price plan from an evaluated bar. All money arithmetic uses integer paisa."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from app.risk.parameters import RiskParameters

Side = Literal["long", "short"]
BPS = 10_000


def floor_tick(price: int, tick: int) -> int:
    return price // tick * tick


def ceil_tick(price: int, tick: int) -> int:
    return -(-price // tick) * tick


@dataclass(frozen=True)
class Levels:
    side: Side
    trigger_paisa: int
    entry_limit_paisa: int
    stop_paisa: int
    target_paisa: int
    risk_per_unit_paisa: int
    reward_per_unit_paisa: int
    reward_risk_bps: int
    support_paisa: int
    atr_paisa: int
    target_basis: Literal["structure", "atr_extension"]
    trailing_activation_paisa: int
    trailing_distance_paisa: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PriceContext:
    close: int
    high: int
    low: int
    atr: int
    tick: int
    structure: tuple[int, ...]


def build_levels(context: PriceContext, side: Side, cfg: RiskParameters) -> Levels | None:
    close, high, low, atr, tick = (
        context.close,
        context.high,
        context.low,
        context.atr,
        context.tick,
    )
    if min(close, low, high, atr, tick) <= 0 or not low <= close <= high:
        return None
    chase = atr * cfg.entry_chase_atr_bps // BPS
    buffer = max(tick, (atr * cfg.stop_buffer_atr_bps + BPS - 1) // BPS)
    extension = atr * cfg.target_extension_atr_bps // BPS
    trail = ceil_tick(max(tick, atr * cfg.trailing_atr_bps // BPS), tick)
    long = side == "long"
    if long:
        trigger = ceil_tick(close, tick)
        limit = max(trigger, floor_tick(close + chase, tick))
        support = max([low, *(n for n in context.structure if low <= n < close)])
        stop = floor_tick(support - buffer, tick)
        candidates = [n for n in context.structure if n > limit]
        target = floor_tick(min(candidates) if candidates else high + extension, tick)
        risk, reward = limit - stop, target - limit
        activation = ceil_tick(
            limit + (risk * cfg.trailing_activation_risk_bps + BPS - 1) // BPS, tick
        )
    else:
        trigger = floor_tick(close, tick)
        limit = min(trigger, ceil_tick(close - chase, tick))
        support = min([high, *(n for n in context.structure if close < n <= high)])
        stop = ceil_tick(support + buffer, tick)
        candidates = [n for n in context.structure if n < limit]
        target = ceil_tick(max(candidates) if candidates else low - extension, tick)
        risk, reward = stop - limit, limit - target
        activation = floor_tick(
            limit - (risk * cfg.trailing_activation_risk_bps + BPS - 1) // BPS, tick
        )
    if min(trigger, limit, stop, target, risk, reward) <= 0:
        return None
    return Levels(
        side,
        trigger,
        limit,
        stop,
        target,
        risk,
        reward,
        reward * BPS // risk,
        support,
        atr,
        "structure" if candidates else "atr_extension",
        activation,
        trail,
    )


def trail_stop(
    side: Side, previous_stop: int, best_price: int, activation: int, distance: int, tick: int
) -> int:
    """Given a confirmed fill's favorable price, move the stop in one direction only."""
    if min(previous_stop, best_price, activation, distance, tick) <= 0:
        raise ValueError("Trailing-stop inputs must be positive")
    if side == "long":
        return (
            max(previous_stop, floor_tick(best_price - distance, tick))
            if best_price >= activation
            else previous_stop
        )
    return (
        min(previous_stop, ceil_tick(best_price + distance, tick))
        if best_price <= activation
        else previous_stop
    )
