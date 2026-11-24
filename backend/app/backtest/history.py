"""Verified historical daily inputs for Phase 6 studies."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.analytics.indicators import Bar
from app.core.tradingdate import MARKET_TZ, canonical_daily_ts

NIFTY_BENCHMARK = "NSE:NIFTY 50"


async def verified_daily_history(
    conn: Any, symbol: str, start_day: date, end_day: date
) -> tuple[tuple[Bar, ...], dict[int, int]]:
    """Return only dates with an NSE-verified equity bhavcopy and matching NIFTY close."""
    stock = await conn.fetch(
        """SELECT b.trade_date,b.open,b.high,b.low,b.close,b.volume
           FROM app.nse_bhav b JOIN app.nse_archive_files f USING (trade_date)
           WHERE b.symbol=$1 AND b.trade_date BETWEEN $2 AND $3 AND f.status='ok'
           ORDER BY b.trade_date""",
        symbol,
        start_day,
        end_day,
    )
    benchmark = await conn.fetch(
        """SELECT ts,close FROM app.ohlcv_candles
           WHERE symbol=$1 AND timeframe='1d' AND ts >= $2 AND ts < $3 ORDER BY ts""",
        NIFTY_BENCHMARK,
        canonical_daily_ts(start_day, MARKET_TZ),
        canonical_daily_ts(end_day + timedelta(days=1), MARKET_TZ),
    )
    bars = tuple(
        Bar(
            int(canonical_daily_ts(row["trade_date"], MARKET_TZ).timestamp()),
            row["open"],
            row["high"],
            row["low"],
            row["close"],
            row["volume"],
        )
        for row in stock
    )
    return bars, {int(row["ts"].timestamp()): row["close"] for row in benchmark}
