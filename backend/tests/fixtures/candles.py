"""Deterministic candle factory (TEST_PLAN: trend / chop / gap / split / bad-bar).

All prices are integer paisa. Daily bars carry the canonical 00:00-IST `ts` and skip
weekends (no exchange-holiday calendar — Phase 1 brings the real one). Same seed, same
candles, every run.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from app.core.tradingdate import MARKET_TZ, canonical_daily_ts

IST = MARKET_TZ


class Pattern(StrEnum):
    TREND = "trend"
    CHOP = "chop"
    GAP = "gap"
    SPLIT = "split"
    BAD_BAR = "bad_bar"


@dataclass(frozen=True)
class Candle:
    symbol: str
    timeframe: str
    ts: datetime  # timezone-aware
    open: int
    high: int
    low: int
    close: int
    volume: int

    def is_well_formed(self) -> bool:
        return (
            min(self.open, self.high, self.low, self.close) > 0
            and self.high >= max(self.open, self.close, self.low)
            and self.low <= min(self.open, self.close)
            and self.volume >= 0
        )


def trading_days(start: date, n: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def make_daily_candles(
    pattern: Pattern,
    n: int = 60,
    *,
    symbol: str = "TESTSYM",
    start: date = date(2024, 1, 1),
    start_close: int = 100_000,  # ₹1,000.00 in paisa
    seed: int = 7,
    split_ratio: int = 2,
) -> list[Candle]:
    if n < 2:
        raise ValueError("need at least 2 candles")
    rng = random.Random(seed)  # noqa: S311 — deterministic test data, not crypto
    days = trading_days(start, n)
    mid = n // 2
    candles: list[Candle] = []
    prev_close = start_close
    for i, d in enumerate(days):
        if pattern is Pattern.TREND:
            drift_bp = 40  # +0.40%/day
        elif pattern is Pattern.CHOP:
            drift_bp = 60 if i % 2 == 0 else -60
        else:
            drift_bp = rng.randint(-30, 30)
        open_ = prev_close
        if pattern is Pattern.GAP and i == mid:
            open_ = prev_close * 108 // 100  # +8% gap-up
        if pattern is Pattern.SPLIT and i == mid:
            open_ = prev_close // split_ratio
        close = max(1, open_ + open_ * drift_bp // 10_000)
        wick = max(1, open_ * rng.randint(10, 80) // 10_000)
        high = max(open_, close) + wick
        low = max(1, min(open_, close) - wick)
        volume = rng.randint(50_000, 500_000)
        if pattern is Pattern.BAD_BAR and i == mid:
            high, low = low, high  # inverted OHLC — must be quarantined, never stored
        candles.append(
            Candle(symbol, "1d", canonical_daily_ts(d, IST), open_, high, low, close, volume)
        )
        prev_close = close
    return candles
