from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, Field, model_validator

Period = Annotated[int, Field(ge=2, le=500)]


class IndicatorPeriods(BaseModel, frozen=True):
    ema: tuple[Period, ...] = (9, 20, 21, 50, 200)
    sma: tuple[Period, ...] = (20, 50, 200)
    rsi: Period = 14
    atr: Period = 14
    adx: Period = 14
    roc: Period = 12
    macd_fast: Period = 12
    macd_slow: Period = 26
    macd_signal: Period = 9
    bands: Period = 20
    bands_deviations: float = Field(default=2, gt=0, le=10)
    keltner: Period = 20
    keltner_atr_multiple: float = Field(default=2, gt=0, le=10)
    stochastic: Period = 14
    stochastic_smooth: Period = 3
    donchian: tuple[Period, ...] = (20, 252)
    volume: Period = 20
    relative_strength: Period = 20
    supertrend_atr_multiple: float = Field(default=3, gt=0, le=10)

    @model_validator(mode="after")
    def validate_periods(self) -> Self:
        if self.macd_fast >= self.macd_slow:
            raise ValueError("MACD fast period must be below the slow period")
        for periods in (self.ema, self.sma, self.donchian):
            if not periods or len(periods) > 12 or len(periods) != len(set(periods)):
                raise ValueError("period lists must have 1 to 12 distinct values")
        if 200 not in self.ema:
            raise ValueError("EMA periods must include 200")
        return self

    def minimum_history(self) -> int:
        return max(
            *self.ema,
            *self.sma,
            self.rsi + 1,
            self.atr + 1,
            2 * self.adx,
            self.roc + 1,
            self.macd_slow + self.macd_signal - 1,
            self.bands,
            self.keltner,
            self.stochastic + 2 * (self.stochastic_smooth - 1),
            max(self.donchian) + 1,
            self.volume + 1,
            self.relative_strength + 1,
        )
