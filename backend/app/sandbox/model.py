"""Daily bar fills, simulation fees and exact paper cost basis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.risk.plans import BPS
from app.sandbox.parameters import PaperParameters

Side = Literal["buy", "sell"]
Kind = Literal["market", "limit", "stop_market", "gtt"]


class Instruction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    symbol: str = Field(pattern=r"^NSE:[A-Z0-9&_.-]{1,64}$")
    side: Side
    kind: Kind
    quantity: int = Field(ge=1, le=1_000_000)
    limit_paisa: int | None = Field(default=None, gt=0, le=10**9)
    trigger_paisa: int | None = Field(default=None, gt=0, le=10**9)
    risk_stop_paisa: int | None = Field(default=None, gt=0, le=10**9)
    risk_target_paisa: int | None = Field(default=None, gt=0, le=10**9)
    trailing_stop_bps: int = Field(default=0, ge=0, le=BPS)
    trailing_stop_paisa_min: int = Field(default=0, ge=0, le=10**9)

    @model_validator(mode="after")
    def prices(self) -> Self:
        if (self.kind in ("limit", "gtt")) != (self.limit_paisa is not None):
            raise ValueError("Only limit and GTT orders require a limit price")
        if (self.kind in ("stop_market", "gtt")) != (self.trigger_paisa is not None):
            raise ValueError("Only stop-market and GTT orders require a trigger price")
        if self.side == "sell" and (
            self.risk_stop_paisa is not None or self.risk_target_paisa is not None
        ):
            raise ValueError("A risk stop applies to new buy orders")
        if self.side == "sell" and (self.trailing_stop_bps or self.trailing_stop_paisa_min):
            raise ValueError("A trailing stop applies to new buy orders")
        if self.trailing_stop_bps and self.risk_stop_paisa is None:
            raise ValueError("A trailing stop requires a risk stop")
        if (
            self.risk_stop_paisa is not None
            and self.risk_target_paisa is not None
            and self.risk_stop_paisa >= self.risk_target_paisa
        ):
            raise ValueError("The risk target must be above the stop")
        return self


@dataclass(frozen=True)
class Bar:
    open: int
    high: int
    low: int
    close: int
    volume: int


@dataclass(frozen=True)
class Match:
    price: int | None
    arm: bool = False


def match(order: Instruction, bar: Bar, armed: bool, config: PaperParameters) -> Match:
    buy = order.side == "buy"
    if order.kind in ("stop_market", "gtt") and not armed:
        trigger = order.trigger_paisa
        if trigger is None:
            return Match(None)
        hit = bar.high >= trigger if buy else bar.low <= trigger
        if not hit:
            return Match(None)
        if order.kind == "gtt":
            return Match(None, arm=True)
        base = max(bar.open, trigger) if buy else min(bar.open, trigger)
    elif order.kind in ("limit", "gtt"):
        limit = order.limit_paisa
        if limit is None or (bar.low > limit if buy else bar.high < limit):
            return Match(None)
        # Improve price at the open; slippage never crosses a requested limit.
        return Match(min(bar.open, limit) if buy else max(bar.open, limit))
    else:
        base = bar.open
    price = adverse_price(base, order.side, config)
    return Match(min(bar.high, price) if buy else max(bar.low, price), arm=False)


def fees(notional: int, side: Side, config: PaperParameters) -> dict[str, int]:
    def charge(ppm: int) -> int:
        return (notional * ppm + 500_000) // 1_000_000

    brokerage = min(charge(config.brokerage_ppm), config.brokerage_cap_paisa)
    exchange = charge(config.exchange_ppm)
    regulator = charge(config.regulator_ppm)
    gst = ((brokerage + exchange + regulator) * config.gst_bps + BPS // 2) // BPS
    return {
        "brokerage": brokerage,
        "transaction_tax": charge(config.buy_tax_ppm if side == "buy" else config.sell_tax_ppm),
        "exchange": exchange,
        "regulator": regulator,
        "stamp": charge(config.stamp_ppm) if side == "buy" else 0,
        "gst": gst,
    }


def sold_cost(total_cost: int, held: int, sold: int) -> int:
    if not 0 < sold <= held or total_cost < 0:
        raise ValueError("Selling requires an adequate paper position")
    return total_cost * sold // held


def adverse_price(base: int, side: Side, config: PaperParameters) -> int:
    """Apply the paper model's integer adverse slippage to a known execution price."""
    slip = (base * config.slippage_bps + BPS - 1) // BPS
    return base + slip if side == "buy" else max(1, base - slip)
