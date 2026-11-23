"""Score a directional option setup using actual, time-aligned captured quotes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from app.core.tradingdate import MARKET_TZ
from app.scoring.parameters import ScoringParameters
from app.scoring.rules import Mode, Strategy, assess, rule

OptionType = Literal["CE", "PE"]


def option_candidate(
    chain: Mapping[str, Any],
    mode: Mode,
    kind: OptionType,
    spot_context: Mapping[str, int | float | None],
    parameters: ScoringParameters,
    *,
    expiry_gamma: bool = False,
) -> dict[str, Any]:
    is_call = kind == "CE"
    side = "call" if is_call else "put"
    identifier = ("expiry_gamma_" if expiry_gamma else "directional_option_") + side
    rules = [
        rule(
            "Underlying close confirms the direction",
            "close_paisa",
            ">" if is_call else "<",
            "vwap_paisa" if mode == "intraday" else "ema_50_paisa",
        ),
        rule(
            "Minimum days to expiry",
            "days_to_expiry",
            ">=",
            0 if expiry_gamma else parameters.option_min_expiry_days,
        ),
        rule(
            "Maximum days to expiry",
            "days_to_expiry",
            "<=",
            1 if expiry_gamma else parameters.option_max_expiry_days,
        ),
        rule("Contract open interest", "oi", ">=", parameters.option_min_oi),
        rule(
            "Two-sided quote spread in basis points",
            "spread_bps",
            "<=",
            parameters.option_max_spread_bps,
        ),
        rule("Implied volatility is calculable", "iv_pct", ">", 0),
    ]
    if expiry_gamma:
        rules.append(
            rule(
                "Spot is beyond the nearest gross gamma wall",
                "spot_paisa",
                ">" if is_call else "<",
                "gamma_wall_paisa",
            )
        )
    strategy = Strategy(
        id=identifier,
        name=f"{'Expiry gamma' if expiry_gamma else 'Directional'} long {kind}",
        mode=mode,
        direction="long" if is_call else "short",
        rules=tuple(rules),
        invalidation="The underlying closes back through the directional trigger.",
    )
    available = chain.get("status") == "ok" and not chain.get("stale") and chain.get("spot_paisa")
    best: dict[str, Any] | None = None
    best_rank: tuple[int, Any, Any, Any, Any] | None = None
    if available:
        spot = chain["spot_paisa"]
        chain_date = datetime.fromisoformat(chain["snapshot_at"]).astimezone(MARKET_TZ).date()
        for expiry in chain["expiries"]:
            if expiry_gamma and expiry["expiry"] != chain_date.isoformat():
                continue
            wall = expiry.get("gamma_wall")
            walls = wall["strike_paisa"] if wall else []
            for contract in expiry["contracts"]:
                if contract["type"] != kind:
                    continue
                mid_twice, bid, ask = (
                    contract["midpoint_twice_paisa"],
                    contract["bid_paisa"],
                    contract["ask_paisa"],
                )
                spread = 2 * (ask - bid) * 10000 / mid_twice if mid_twice and bid and ask else None
                values = {
                    **spot_context,
                    "days_to_expiry": expiry["days_to_expiry"],
                    "oi": contract["oi"],
                    "spread_bps": spread,
                    "iv_pct": contract["iv_pct"],
                    "spot_paisa": spot,
                    "gamma_wall_paisa": min(walls, key=lambda x: abs(x - spot)) if walls else None,
                }
                result = assess(strategy, values, {}, True)  # both call and put are long premium
                rank = (
                    result["status"] != "matched",
                    expiry["expiry"],
                    abs(contract["strike_paisa"] - spot),
                    spread if spread is not None else float("inf"),
                    contract["strike_paisa"],
                )
                if best_rank is None or rank < best_rank:
                    best = {
                        **result,
                        "contract": {
                            "type": kind,
                            "expiry": expiry["expiry"],
                            "strike_paisa": contract["strike_paisa"],
                            "bid_paisa": bid,
                            "ask_paisa": ask,
                            "iv_pct": contract["iv_pct"],
                            "oi": contract["oi"],
                            "snapshot_at": chain.get("snapshot_at"),
                        },
                    }
                    best_rank = rank
    if best is not None:
        return best
    return {
        **assess(strategy, spot_context, {}, True),
        "reason": "No eligible saved chain"
        if available
        else "A fresh, complete option chain is required",
        "contract": None,
    }


def futures_candidate(
    item: dict[str, Any],
    data: Mapping[str, Any] | None,
    parameters: ScoringParameters,
) -> dict[str, Any]:
    """A daily underlying trigger with an independently verified NSE futures contract."""
    contract_strategy = Strategy(
        id=item["id"],
        name=item["name"],
        mode="swing",
        direction=item["direction"],
        revision=item["revision"],
        rules=(
            rule("Contract has a positive close", "futures_close_paisa", ">", 0),
            rule("Contracts traded", "futures_volume", ">=", parameters.futures_min_volume),
            rule("Contracts open", "futures_oi", ">=", parameters.futures_min_oi),
        ),
    )
    context = {
        "futures_close_paisa": data["close_paisa"] if data else None,
        "futures_volume": data["contracts"] if data else None,
        "futures_oi": data["open_interest"] if data else None,
    }
    contract = assess(contract_strategy, context, {}, True)
    return {
        **item,
        "instrument": "future",
        "status": contract["status"] if item["status"] == "matched" else item["status"],
        "evidence": [*item["evidence"], *contract["evidence"]],
        "contract": {
            "type": "FUT",
            "expiry": data["expiry"].isoformat(),
            "close_paisa": data["close_paisa"],
            "volume": data["contracts"],
            "oi": data["open_interest"],
            "trade_date": data["trade_date"].isoformat(),
        }
        if data
        else None,
        "reason": None if data else "No validated futures contract for the evaluated session",
    }
