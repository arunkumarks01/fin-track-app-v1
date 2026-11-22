"""Point-in-time correlation of aligned adjusted daily returns."""

from __future__ import annotations

import math
from datetime import date, datetime
from itertools import pairwise
from typing import Any


def correlation_bps(left: list[int], right: list[int], minimum: int) -> int | None:
    """Pearson correlation of returns, rounded upward for conservative risk checks."""
    if len(left) != len(right) or len(left) < minimum + 1:
        return None
    if any(p <= 0 for p in left + right):
        return None
    x = [b / a - 1 for a, b in pairwise(left)]
    y = [b / a - 1 for a, b in pairwise(right)]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    xx = sum((v - mx) ** 2 for v in x)
    yy = sum((v - my) ** 2 for v in y)
    if xx <= 0 or yy <= 0:
        return None
    result = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / math.sqrt(xx * yy)
    return max(-10000, min(10000, math.ceil(result * 10000)))


async def portfolio_correlation(
    conn: Any, symbol: str, positions: list[str], before: datetime, window: int, minimum: int
) -> int | None:
    """Use only pre-fill candles, and fail closed when any held pair lacks data."""
    others = [s for s in positions if s != symbol]
    if not others:
        return 0
    rows = await conn.fetch(
        """SELECT symbol, ts, close FROM (
               SELECT symbol, ts, close, row_number() OVER (PARTITION BY symbol ORDER BY ts DESC) n
               FROM app.ohlcv_candles WHERE symbol=ANY($1::text[]) AND timeframe='1d' AND ts < $2
           ) bars WHERE n <= $3 ORDER BY ts""",
        [symbol, *others],
        before,
        window + 1,
    )
    series: dict[str, dict[date, int]] = {s: {} for s in [symbol, *others]}
    for row in rows:
        series[row["symbol"]][row["ts"].date()] = row["close"]
    correlations = []
    for other in others:
        common = sorted(series[symbol].keys() & series[other].keys())
        value = correlation_bps(
            [series[symbol][d] for d in common], [series[other][d] for d in common], minimum
        )
        if value is None:
            return None
        correlations.append(value)
    return max(correlations)
