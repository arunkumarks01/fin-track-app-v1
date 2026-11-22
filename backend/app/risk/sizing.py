"""Pure position sizing and portfolio checks for the paper planning interface."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any

from app.risk.parameters import PaperMode, RiskParameters, SizingMode
from app.risk.plans import BPS, Levels


@dataclass(frozen=True)
class Kelly:
    wins: int
    outcomes: int
    average_win_paisa: int
    average_loss_paisa: int
    validated_days: int


@dataclass(frozen=True)
class Portfolio:
    cash_paisa: int
    realized_pnl_paisa: int
    open_positions: int
    trades_today: int
    open_risk_paisa: int | None = None
    sector_notional_paisa: int | None = None
    correlation_bps: int | None = None
    required_margin_paisa: int | None = None


@dataclass(frozen=True)
class Sizing:
    mode: SizingMode
    lots: int | None
    units: int | None
    risk_paisa: int | None
    notional_paisa: int | None
    risk_budget_paisa: int
    notional_cap_paisa: int
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def size(
    levels: Levels,
    lot_size: int,
    instrument: str,
    cfg: RiskParameters,
    mode: SizingMode,
    portfolio: Portfolio | None = None,
    kelly: Kelly | None = None,
) -> Sizing:
    if lot_size < 1:
        raise ValueError("Lot size must be positive")
    capital = cfg.paper_capital_paisa
    budget = capital * cfg.risk_per_trade_bps // BPS
    cap = capital * cfg.max_notional_bps // BPS
    if mode == "kelly":
        if (
            kelly is None
            or kelly.validated_days < cfg.kelly_min_days
            or not 0 < kelly.wins < kelly.outcomes
            or min(kelly.average_win_paisa, kelly.average_loss_paisa) <= 0
        ):
            return Sizing(
                mode, None, None, None, None, budget, cap, "Validated Kelly history unavailable"
            )
        probability = Fraction(kelly.wins, kelly.outcomes)
        fraction = probability - (1 - probability) * Fraction(
            kelly.average_loss_paisa, kelly.average_win_paisa
        )
        budget = min(budget, max(0, int(capital * fraction * cfg.kelly_fraction_bps / BPS)))
    risk_per_lot = levels.risk_per_unit_paisa * lot_size
    gross_entry = max(levels.trigger_paisa, levels.entry_limit_paisa)
    # Options gross notional here is premium paid, before costs. Futures uses contract value.
    notional_per_lot = gross_entry * lot_size
    lots = (
        cfg.fixed_lots
        if mode == "fixed_lots"
        else min(budget // risk_per_lot, cap // notional_per_lot)
    )
    if (
        mode != "fixed_lots"
        and portfolio
        and portfolio.correlation_bps is not None
        and cfg.correlation_limit_bps is not None
        and portfolio.correlation_bps >= cfg.correlation_limit_bps
    ):
        lots = lots * cfg.correlation_size_bps // BPS
    return Sizing(
        mode,
        lots,
        lots * lot_size,
        lots * risk_per_lot,
        lots * notional_per_lot,
        budget,
        cap,
        "Futures contract notional. Margin is separate." if instrument == "future" else None,
    )


def checks(
    reward_risk_bps: int,
    sizing: Sizing,
    portfolio: Portfolio | None,
    cfg: RiskParameters,
    mode: PaperMode,
    instrument: str,
) -> dict[str, Any]:
    warnings = []
    unknown = []
    if sizing.lots is None:
        unknown.append("Position sizing unavailable")
    elif sizing.lots == 0:
        warnings.append("Risk and notional budgets do not fit one lot")
    if sizing.risk_paisa is not None and sizing.risk_paisa > sizing.risk_budget_paisa:
        warnings.append("Per-trade risk budget exceeded")
    if sizing.notional_paisa is not None and sizing.notional_paisa > sizing.notional_cap_paisa:
        warnings.append("Per-trade notional cap exceeded")
    if reward_risk_bps < cfg.min_reward_risk_bps:
        warnings.append("Reward-to-risk is below the configured minimum")
    if portfolio is None:
        unknown.append("Paper portfolio balances and limits are not yet available")
    else:
        capital = cfg.paper_capital_paisa
        if portfolio.realized_pnl_paisa <= -(capital * cfg.daily_loss_bps // BPS):
            warnings.append("Daily loss circuit breaker reached")
        if portfolio.trades_today >= cfg.max_daily_trades:
            warnings.append("Daily trade count reached")
        if portfolio.open_positions >= cfg.max_positions:
            warnings.append("Maximum open positions reached")
        required_cash = (
            portfolio.required_margin_paisa if instrument == "future" else sizing.notional_paisa
        )
        if instrument == "future" and required_cash is None:
            unknown.append("Verified futures margin is required")
        elif (
            required_cash is not None
            and required_cash > portfolio.cash_paisa - capital * cfg.cash_buffer_bps // BPS
        ):
            warnings.append("Cash buffer would be breached")
        for value, cap, additional, name in (
            (portfolio.open_risk_paisa, cfg.max_heat_bps, sizing.risk_paisa, "Portfolio heat"),
            (
                portfolio.sector_notional_paisa,
                cfg.max_sector_notional_bps,
                sizing.notional_paisa,
                "Sector exposure",
            ),
        ):
            if cap is not None:
                if value is None or additional is None:
                    unknown.append(f"{name} unavailable")
                elif value + additional > capital * cap // BPS:
                    warnings.append(f"{name} cap exceeded")
        if cfg.correlation_limit_bps is not None and portfolio.correlation_bps is None:
            unknown.append("Verified correlation unavailable")
    return {
        "mode": mode,
        "blocks": mode == "realistic" and bool(warnings or unknown),
        "warnings": warnings,
        "unknown": unknown,
    }
