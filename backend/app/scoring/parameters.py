from typing import Self

from pydantic import BaseModel, Field, model_validator


class ScoringParameters(BaseModel, frozen=True):
    rsi_low: float = Field(default=30, gt=0, lt=50)
    rsi_high: float = Field(default=70, gt=50, lt=100)
    squeeze_width_pct: float = Field(default=5, gt=0, le=100)
    gap_min_pct: float = Field(default=1, gt=0, le=100)
    momentum_min_relative_volume: float = Field(default=1.5, gt=0)
    futures_min_volume: int = Field(default=100, ge=1)
    futures_min_oi: int = Field(default=1000, ge=1)
    option_min_oi: int = Field(default=1000, ge=1)
    option_max_spread_bps: int = Field(default=500, ge=1, le=10000)
    option_min_expiry_days: int = Field(default=1, ge=0, le=30)
    option_max_expiry_days: int = Field(default=35, ge=1, le=90)
    min_calibration_observations: int = Field(default=30, ge=2, le=10000)
    news_timeout_seconds: float = Field(default=15, gt=0, le=60)
    news_max_age_hours: int = Field(default=48, ge=1, le=168)
    news_max_items: int = Field(default=8, ge=1, le=25)
    news_failure_cooldown_seconds: int = Field(default=120, ge=1, le=3600)

    @model_validator(mode="after")
    def expiry_bounds(self) -> Self:
        if self.option_max_expiry_days < self.option_min_expiry_days:
            raise ValueError("maximum option expiry must be at or above minimum")
        return self
