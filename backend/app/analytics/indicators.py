"""Deterministic, causal indicator series. Price calculations use paisa as their base unit."""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from math import fsum
from statistics import pstdev

from app.analytics.parameters import IndicatorPeriods

Series = list[float | None]


@dataclass(frozen=True)
class Bar:
    t: int
    open: int
    high: int
    low: int
    close: int
    volume: int

    def validate(self) -> None:
        if (
            self.low <= 0
            or not (
                self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high
            )
            or self.volume < 0
        ):
            raise ValueError("invalid OHLCV bar")


def _average(values: Sequence[float], period: int, alpha: float) -> Series:
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    avg = fsum(values[:period]) / period
    out[period - 1] = avg
    for i in range(period, len(values)):
        avg += (values[i] - avg) * alpha
        out[i] = avg
    return out


def sma(values: Sequence[float], period: int) -> Series:
    return [None] * min(len(values), period - 1) + [
        fsum(values[i + 1 - period : i + 1]) / period for i in range(period - 1, len(values))
    ]


def ema(values: Sequence[float], period: int) -> Series:
    return _average(values, period, 2 / (period + 1))


def _wilder(values: Sequence[float], period: int) -> Series:
    return _average(values, period, 1 / period)


def rsi(closes: Sequence[int], period: int) -> Series:
    if not closes:
        return []
    change = [b - a for a, b in itertools.pairwise(closes)]
    gain = _wilder([max(d, 0) for d in change], period)
    loss = _wilder([max(-d, 0) for d in change], period)
    out: Series = [None]
    for g, loss_value in zip(gain, loss, strict=True):
        if g is None or loss_value is None:
            out.append(None)
        else:
            out.append(100 * g / (g + loss_value) if g + loss_value else 0.0)
    return out


def true_range(bars: Sequence[Bar]) -> list[int]:
    return [
        max(now.high - now.low, abs(now.high - prev.close), abs(now.low - prev.close))
        for prev, now in itertools.pairwise(bars)
    ]


def atr(bars: Sequence[Bar], period: int) -> Series:
    return [None, *_wilder(true_range(bars), period)] if bars else []


def macd(closes: Sequence[int], fast: int, slow: int, signal: int) -> tuple[Series, Series, Series]:
    slow_line = ema(closes, slow)
    fast_line = ema(closes[slow - fast :], fast)
    values = [
        f - s
        for f, s in zip(fast_line[fast - 1 :], slow_line[slow - 1 :], strict=True)
        if f is not None and s is not None
    ]
    sig = ema(values, signal)
    prefix = [None] * min(len(closes), slow - 1)
    return (
        [*prefix, *(v if s is not None else None for v, s in zip(values, sig, strict=True))],
        [*prefix, *sig],
        [*prefix, *(v - s if s is not None else None for v, s in zip(values, sig, strict=True))],
    )


def directional(bars: Sequence[Bar], period: int) -> tuple[Series, Series, Series]:
    """Wilder DI and ADX, seeded with one fewer move than the period."""
    n = len(bars)
    plus: Series = [None] * n
    minus: Series = [None] * n
    adx: Series = [None] * n
    if n <= period:
        return plus, minus, adx
    ranges = true_range(bars)
    moves = [(b.high - a.high, a.low - b.low) for a, b in itertools.pairwise(bars)]
    up = [u if u > max(d, 0) else 0 for u, d in moves]
    down = [d if d > max(u, 0) else 0 for u, d in moves]
    tr, pdm, mdm = (fsum(arr[: period - 1]) for arr in (ranges, up, down))
    dx: list[float] = []
    for i in range(period, n):
        tr = tr - tr / period + ranges[i - 1]
        pdm = pdm - pdm / period + up[i - 1]
        mdm = mdm - mdm / period + down[i - 1]
        p, m = (100 * pdm / tr, 100 * mdm / tr) if tr else (0.0, 0.0)
        plus[i], minus[i] = p, m
        dx.append(100 * abs(p - m) / (p + m) if p + m else 0.0)
    adx[period:] = _wilder(dx, period)
    return plus, minus, adx


def bollinger(
    closes: Sequence[int], period: int, deviations: float
) -> tuple[Series, Series, Series]:
    center = sma(closes, period)
    upper: Series = [None] * len(closes)
    lower: Series = [None] * len(closes)
    for i, mean in enumerate(center):
        if mean is not None:
            width = deviations * pstdev(closes[i + 1 - period : i + 1], mu=mean)
            upper[i], lower[i] = mean + width, mean - width
    return upper, center, lower


def stochastic(bars: Sequence[Bar], period: int, smooth: int) -> tuple[Series, Series]:
    raw = []
    for end in range(period, len(bars) + 1):
        window = bars[end - period : end]
        hi, lo = max(b.high for b in window), min(b.low for b in window)
        raw.append(100 * (window[-1].close - lo) / (hi - lo) if hi > lo else 0.0)
    slow_k = sma(raw, smooth)
    slow_d = sma([value for value in slow_k if value is not None], smooth)
    head: Series = [None] * min(len(bars), period + smooth - 2)
    k = [value for value in slow_k if value is not None]
    return [*head, *(v if d is not None else None for v, d in zip(k, slow_d, strict=True))], [
        *head,
        *slow_d,
    ]


def obv(bars: Sequence[Bar]) -> list[int]:
    if not bars:
        return []
    value = bars[0].volume
    out = [value]
    for prev, bar in itertools.pairwise(bars):
        value += ((bar.close > prev.close) - (bar.close < prev.close)) * bar.volume
        out.append(value)
    return out


def _supertrend(bars: Sequence[Bar], ranges: Series, multiple: float) -> Series:
    upper = lower = 0.0
    bullish = False
    out: Series = []
    for i, (bar, width) in enumerate(zip(bars, ranges, strict=True)):
        if width is None:
            out.append(None)
            continue
        mid = (bar.high + bar.low) / 2
        next_upper, next_lower = mid + multiple * width, mid - multiple * width
        if i and out[-1] is not None:
            prev_close = bars[i - 1].close
            next_upper = min(upper, next_upper) if prev_close <= upper else next_upper
            next_lower = max(lower, next_lower) if prev_close >= lower else next_lower
            bullish = bar.close >= next_lower if bullish else bar.close > next_upper
        upper, lower = next_upper, next_lower
        out.append(lower if bullish else upper)
    return out


def calculate(bars: Sequence[Bar], settings: IndicatorPeriods) -> dict[str, Series]:
    """One output per bar. Stateful indicators only see the contiguous input supplied."""
    for i, bar in enumerate(bars):
        bar.validate()
        if i and bars[i - 1].t >= bar.t:
            raise ValueError("OHLCV timestamps must be strictly increasing")
    close, volume = [b.close for b in bars], [b.volume for b in bars]
    ranges = atr(bars, settings.atr)
    macd_line, signal, macd_hist = macd(
        close, settings.macd_fast, settings.macd_slow, settings.macd_signal
    )
    di_plus, di_minus, adx = directional(bars, settings.adx)
    bb_hi, bb_mid, bb_lo = bollinger(close, settings.bands, settings.bands_deviations)
    keltner_mid = ema(close, settings.keltner)
    widths = [w * settings.keltner_atr_multiple if w is not None else None for w in ranges]
    stoch_k, stoch_d = stochastic(bars, settings.stochastic, settings.stochastic_smooth)
    average_volume = sma(volume, settings.volume)
    out: dict[str, Series] = {
        **{f"ema_{period}_paisa": ema(close, period) for period in settings.ema},
        **{f"sma_{period}_paisa": sma(close, period) for period in settings.sma},
        "rsi_pct": rsi(close, settings.rsi),
        "atr_paisa": ranges,
        "adx": adx,
        "di_plus": di_plus,
        "di_minus": di_minus,
        "macd_paisa": macd_line,
        "macd_signal_paisa": signal,
        "macd_histogram_paisa": macd_hist,
        "bollinger_upper_paisa": bb_hi,
        "bollinger_middle_paisa": bb_mid,
        "bollinger_lower_paisa": bb_lo,
        "keltner_middle_paisa": keltner_mid,
        "stochastic_k_pct": stoch_k,
        "stochastic_d_pct": stoch_d,
        "obv": list(obv(bars)),
        "volume_sma": average_volume,
        "supertrend_paisa": _supertrend(bars, ranges, settings.supertrend_atr_multiple),
        "roc_pct": [
            None if i < settings.roc else 100 * (value / close[i - settings.roc] - 1)
            for i, value in enumerate(close)
        ],
        "relative_volume": [
            volume[i] / base if i and (base := average_volume[i - 1]) else None
            for i in range(len(volume))
        ],
    }
    out["keltner_upper_paisa"] = [
        m + w if m is not None and w is not None else None
        for m, w in zip(keltner_mid, widths, strict=True)
    ]
    out["keltner_lower_paisa"] = [
        m - w if m is not None and w is not None else None
        for m, w in zip(keltner_mid, widths, strict=True)
    ]
    for period in settings.donchian:
        out[f"prior_{period}_high_paisa"] = [
            max(b.high for b in bars[i - period : i]) if i >= period else None
            for i in range(len(bars))
        ]
        out[f"prior_{period}_low_paisa"] = [
            min(b.low for b in bars[i - period : i]) if i >= period else None
            for i in range(len(bars))
        ]
    return out
