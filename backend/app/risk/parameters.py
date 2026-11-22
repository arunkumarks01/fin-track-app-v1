from datetime import time
from typing import Literal

from pydantic import BaseModel, Field

PaperMode = Literal["learning", "realistic"]
SizingMode = Literal["fixed_risk", "fixed_lots", "kelly"]


class RiskParameters(BaseModel, frozen=True):
    paper_capital_paisa: int = Field(default=1_000_000_000, gt=0, le=10**14)
    risk_per_trade_bps: int = Field(default=100, ge=1, le=10_000)
    max_notional_bps: int = Field(default=1000, ge=1, le=10_000)
    cash_buffer_bps: int = Field(default=2000, ge=0, lt=10_000)
    daily_loss_bps: int = Field(default=300, ge=1, le=10_000)
    max_positions: int = Field(default=5, ge=1)
    max_daily_trades: int = Field(default=5, ge=1)
    max_heat_bps: int | None = Field(default=None, ge=1, le=10_000)
    max_sector_notional_bps: int | None = Field(default=None, ge=1, le=10_000)
    correlation_limit_bps: int | None = Field(default=None, ge=1, le=10_000)
    correlation_size_bps: int = Field(default=5000, ge=1, le=10_000)
    correlation_window_sessions: int = Field(default=60, ge=20, le=500)
    correlation_min_observations: int = Field(default=20, ge=10, le=500)
    stop_buffer_atr_bps: int = Field(default=2500, ge=1, le=50_000)
    entry_chase_atr_bps: int = Field(default=2500, ge=1, le=50_000)
    target_extension_atr_bps: int = Field(default=20000, ge=1, le=100_000)
    min_reward_risk_bps: int = Field(default=15000, ge=1, le=100_000)
    trailing_activation_risk_bps: int = Field(default=10000, ge=1, le=100_000)
    trailing_atr_bps: int = Field(default=15000, ge=1, le=100_000)
    entry_cutoff_ist: time = time(15, 0)
    fixed_lots: int = Field(default=1, ge=1, le=10_000)
    kelly_min_days: int = Field(default=180, ge=180, le=3650)
    kelly_fraction_bps: int = Field(default=2500, ge=1, le=10_000)
