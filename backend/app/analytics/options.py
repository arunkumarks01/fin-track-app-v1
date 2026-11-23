"""Read-time European option analytics. No inferred dealer positions or synthetic quotes."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from math import erfc, exp, isfinite, log, pi, sqrt
from typing import Any, Literal

OptionType = Literal["CE", "PE"]
_DAYS_PER_YEAR = 365  # ACT/365 fixed


@dataclass(frozen=True)
class Contract:
    expiry: date
    strike: int
    kind: OptionType
    bids: int | None
    asks: int | None
    bid_qty: int | None
    ask_qty: int | None
    oi: int | None
    volume: int | None

    @property
    def key(self) -> tuple[date, int, OptionType]:
        return self.expiry, self.strike, self.kind

    def midpoint(self) -> float | None:
        total = self.midpoint_twice_paisa()
        return total / 2 if total is not None else None

    def midpoint_twice_paisa(self) -> int | None:
        if (
            self.bids is None
            or self.asks is None
            or not 0 < self.bids <= self.asks
            or not self.bid_qty
            or not self.ask_qty
        ):
            return None
        return self.bids + self.asks


@dataclass(frozen=True)
class Model:
    spot: int
    strike: int
    years: float
    rate: float
    dividend: float
    kind: OptionType

    def discounts(self) -> tuple[float, float]:
        return exp(-self.dividend * self.years), exp(-self.rate * self.years)

    def d_values(self, volatility: float) -> tuple[float, float]:
        width = volatility * sqrt(self.years)
        d1 = (log(self.spot / self.strike) + (self.rate - self.dividend) * self.years) / width
        d1 += width / 2
        return d1, d1 - width

    def price(self, volatility: float) -> float:
        d1, d2 = self.d_values(volatility)
        ds, dk = self.discounts()
        sign = 1 if self.kind == "CE" else -1
        return sign * (self.spot * ds * _cdf(sign * d1) - self.strike * dk * _cdf(sign * d2))


def _cdf(value: float) -> float:
    return erfc(-value / sqrt(2)) / 2


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma_per_paisa: float
    theta_paisa_per_day: float
    vega_paisa_per_vol_point: float
    rho_paisa_per_rate_point: float


def greeks(model: Model, volatility: float) -> Greeks:
    d1, d2 = model.d_values(volatility)
    ds, dk = model.discounts()
    sign = 1 if model.kind == "CE" else -1
    density = exp(-d1 * d1 / 2) / sqrt(2 * pi)
    delta = sign * ds * _cdf(sign * d1)
    gamma = ds * density / (model.spot * volatility * sqrt(model.years))
    theta = (
        -model.spot * ds * density * volatility / (2 * sqrt(model.years))
        - sign * model.rate * model.strike * dk * _cdf(sign * d2)
        + sign * model.dividend * model.spot * ds * _cdf(sign * d1)
    )
    vega = model.spot * ds * density * sqrt(model.years)
    rho = sign * model.strike * dk * model.years * _cdf(sign * d2)
    return Greeks(delta, gamma, theta / _DAYS_PER_YEAR, vega / 100, rho / 100)


def implied_volatility(model: Model, premium: float, ceiling: float) -> float | None:
    if min(model.spot, model.strike, model.years, premium, ceiling) <= 0 or not all(
        isfinite(x) for x in (model.years, model.rate, model.dividend, premium, ceiling)
    ):
        return None
    ds, dk = model.discounts()
    s, k = model.spot * ds, model.strike * dk
    lower, upper = (max(s - k, 0), s) if model.kind == "CE" else (max(k - s, 0), k)
    if not lower < premium < upper or model.price(ceiling) < premium:
        return None
    lo, hi = 0.0, ceiling
    for _ in range(64):
        mid = (lo + hi) / 2
        if model.price(mid) < premium:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def constant_tenor_iv(points: Mapping[float, float], target_years: float) -> float | None:
    """Interpolate total variance at a bracketed tenor. Never extrapolate across expiries."""
    if target_years in points:
        return points[target_years]
    below = max((t for t in points if t < target_years), default=None)
    above = min((t for t in points if t > target_years), default=None)
    if below is None or above is None:
        return None
    weight = (target_years - below) / (above - below)
    variance = (1 - weight) * below * points[below] ** 2 + weight * above * points[above] ** 2
    return sqrt(variance / target_years)


def iv_rank(current: float | None, history: Sequence[float], minimum: int) -> float | None:
    """Rank from 0 to 100 in the observed range, including the current observation."""
    if current is None or len(history) + 1 < minimum:
        return None
    low, high = min(current, *history), max(current, *history)
    return 100 * (current - low) / (high - low) if high > low else None


def _pairs(contracts: Sequence[Contract]) -> dict[int, dict[OptionType, Contract]]:
    grouped: dict[int, dict[OptionType, Contract]] = defaultdict(dict)
    for contract in contracts:
        group = grouped[contract.strike]
        if contract.kind in group:
            raise ValueError("duplicate option contract in snapshot")
        group[contract.kind] = contract
    return grouped


def _max_pain(calls: Mapping[int, int], puts: Mapping[int, int]) -> tuple[list[int], int]:
    """Find all tied minima within the captured strikes in a single cumulative pass."""
    out: list[int] = []
    minimum: int | None = None
    put_oi, put_value = sum(puts.values()), sum(k * q for k, q in puts.items())
    call_oi = call_value = 0
    for strike in sorted(calls):
        put_oi -= puts[strike]
        put_value -= strike * puts[strike]
        payout = strike * call_oi - call_value + put_value - strike * put_oi
        if minimum is None or payout < minimum:
            minimum, out = payout, [strike]
        elif payout == minimum:
            out.append(strike)
        call_oi += calls[strike]
        call_value += strike * calls[strike]
    if minimum is None:
        raise ValueError("max pain requires paired strikes")
    return out, minimum


def _ratio(puts: Sequence[int | None], calls: Sequence[int | None]) -> float | None:
    if None in puts or None in calls:
        return None
    total_calls = sum(v or 0 for v in calls)
    return sum(v or 0 for v in puts) / total_calls if total_calls else None


def _model_values(
    contract: Contract,
    spot: int | None,
    years: float,
    rates: tuple[float, float] | None,
    ceiling: float,
) -> tuple[float | None, Greeks | None, str | None]:
    if years <= 0:
        return None, None, "expired"
    mid = contract.midpoint()
    if mid is None:
        return None, None, "missing_or_crossed_quote"
    if not spot:
        return None, None, "missing_spot"
    if rates is None:
        return None, None, "pricing_inputs_required"
    model = Model(spot, contract.strike, years, *rates, contract.kind)
    iv = implied_volatility(model, mid, ceiling)
    if iv is None:
        return None, None, "premium_outside_model_bounds"
    return iv, greeks(model, iv), None


def expiry_analytics(
    contracts: Sequence[Contract],
    previous: Mapping[tuple[date, int, OptionType], Contract],
    spot: int | None,
    years: float,
    rates: tuple[float, float] | None,
    ceiling: float,
    complete: bool,
) -> dict[str, Any]:
    pairs = _pairs(contracts)
    call_oi = {k: p["CE"].oi for k, p in pairs.items() if "CE" in p}
    put_oi = {k: p["PE"].oi for k, p in pairs.items() if "PE" in p}
    calls = {k: q for k, q in call_oi.items() if q is not None}
    puts = {k: q for k, q in put_oi.items() if q is not None}
    paired = bool(complete and pairs and all(len(p) == 2 for p in pairs.values()))
    all_oi = paired and len(calls) == len(puts) == len(pairs)
    metrics_available = paired and years > 0
    total_oi = sum(calls.values()) + sum(puts.values())
    strikes, payout = (
        _max_pain(calls, puts) if metrics_available and all_oi and total_oi > 0 else (None, None)
    )

    rows = []
    exposure: dict[int, float] = defaultdict(float)
    covered_oi = 0
    ivs: dict[int, dict[OptionType, float]] = defaultdict(dict)
    for contract in contracts:
        iv, sensitivities, reason = _model_values(contract, spot, years, rates, ceiling)
        before = previous.get(contract.key)
        before_oi = before.oi if before else None
        before_mid = before.midpoint_twice_paisa() if before else None
        mid = contract.midpoint_twice_paisa()
        change = (
            contract.oi - before_oi if contract.oi is not None and before_oi is not None else None
        )
        premium_change = mid - before_mid if mid is not None and before_mid is not None else None
        pattern = None
        if change and premium_change:
            price_direction = "up" if premium_change > 0 else "down"
            oi_direction = "up" if change > 0 else "down"
            pattern = f"premium_{price_direction}_oi_{oi_direction}"
        if iv is not None:
            ivs[contract.strike][contract.kind] = iv
        if contract.oi and sensitivities is not None and spot:
            covered_oi += contract.oi
            exposure[contract.strike] += (
                sensitivities.gamma_per_paisa * spot**2 * 0.01 * contract.oi
            )
        rows.append(
            {
                "strike_paisa": contract.strike,
                "type": contract.kind,
                "bid_paisa": contract.bids,
                "ask_paisa": contract.asks,
                "midpoint_twice_paisa": mid,
                "oi": contract.oi,
                "volume": contract.volume,
                "previous_snapshot_oi": before_oi,
                "oi_change": change,
                "premium_change_twice_paisa": premium_change,
                "oi_premium_pattern": pattern,
                "iv_pct": iv * 100 if iv is not None else None,
                "greeks": asdict(sensitivities) if sensitivities is not None else None,
                "unavailable_reason": reason,
            }
        )

    strongest = max(exposure.values(), default=0)
    gamma_wall = None
    if metrics_available and all_oi and covered_oi == total_oi and strongest > 0:
        gamma_wall = {
            "strike_paisa": sorted(k for k, value in exposure.items() if value == strongest),
            "gross_gamma_exposure_paisa_per_1pct": round(strongest),
        }
    atm_iv = None
    if metrics_available and spot and pairs:
        atm = min(pairs, key=lambda k: (abs(k - spot), k))
        if len(ivs[atm]) == 2:
            atm_iv = sum(ivs[atm].values()) / 2
    return {
        "pcr_oi": _ratio(list(put_oi.values()), list(call_oi.values()))
        if metrics_available
        else None,
        "pcr_volume": _ratio(
            [p["PE"].volume for p in pairs.values()], [p["CE"].volume for p in pairs.values()]
        )
        if metrics_available
        else None,
        "max_pain_strikes_paisa": strikes,
        "minimum_payout_paisa": payout,
        "gamma_wall": gamma_wall,
        "oi_with_model_greeks": covered_oi,
        "total_known_oi": total_oi,
        "atm_iv_pct": atm_iv * 100 if atm_iv is not None else None,
        "complete_pairs": paired,
        "contracts": rows,
    }
