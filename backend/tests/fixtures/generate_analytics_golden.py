"""Generate pinned TA-Lib/QuantLib reference values without project dependencies.

Run from backend with TA-Lib 0.6.8 and QuantLib 1.40 on PYTHONPATH:
    python -m tests.fixtures.generate_analytics_golden
"""

from __future__ import annotations

import json
from importlib import import_module
from math import isfinite
from pathlib import Path
from typing import Any

from tests.fixtures.candles import Pattern, make_daily_candles

GOLDEN = Path(__file__).with_name("analytics_golden.json")
SAMPLE_INDICES = (0, 13, 14, 19, 26, 27, 33, 50, 63, 198, 199, 200, 251, 252, 259)
BAR_COUNT = 260


def technical(np: Any, ta: Any, pattern: Pattern) -> dict[str, list[float | None]]:
    bars = make_daily_candles(pattern, n=BAR_COUNT)
    hi, lo, close, volume = (
        np.asarray([getattr(bar, key) for bar in bars], dtype=float)
        for key in ("high", "low", "close", "volume")
    )
    macd, signal, histogram = ta.MACD(close)
    bb_upper, bb_mid, bb_lower = ta.BBANDS(close, timeperiod=20)
    slow_k, slow_d = ta.STOCH(hi, lo, close, fastk_period=14)
    expected = {
        "ema_9_paisa": ta.EMA(close, timeperiod=9),
        "ema_20_paisa": ta.EMA(close, timeperiod=20),
        "ema_21_paisa": ta.EMA(close, timeperiod=21),
        "ema_50_paisa": ta.EMA(close, timeperiod=50),
        "ema_200_paisa": ta.EMA(close, timeperiod=200),
        "sma_20_paisa": ta.SMA(close, timeperiod=20),
        "sma_50_paisa": ta.SMA(close, timeperiod=50),
        "sma_200_paisa": ta.SMA(close, timeperiod=200),
        "rsi_pct": ta.RSI(close),
        "atr_paisa": ta.ATR(hi, lo, close),
        "adx": ta.ADX(hi, lo, close),
        "di_plus": ta.PLUS_DI(hi, lo, close),
        "di_minus": ta.MINUS_DI(hi, lo, close),
        "macd_paisa": macd,
        "macd_signal_paisa": signal,
        "macd_histogram_paisa": histogram,
        "bollinger_upper_paisa": bb_upper,
        "bollinger_middle_paisa": bb_mid,
        "bollinger_lower_paisa": bb_lower,
        "stochastic_k_pct": slow_k,
        "stochastic_d_pct": slow_d,
        "obv": ta.OBV(close, volume),
        "roc_pct": ta.ROC(close, timeperiod=12),
        "volume_sma": ta.SMA(volume, timeperiod=20),
    }
    return {
        name: [round(float(series[i]), 10) if isfinite(series[i]) else None for i in SAMPLE_INDICES]
        for name, series in expected.items()
    }


def european(ql: Any) -> list[dict[str, Any]]:
    now = ql.Date(23, 11, 2026)
    ql.Settings.instance().evaluationDate = now
    dc = ql.Actual365Fixed()
    output = []
    for kind, spot, strike, days, rate, dividend, iv in (
        ("CE", 1_230_000, 1_250_000, 45, 0.06, 0.014, 0.23),
        ("PE", 1_230_000, 1_250_000, 45, 0.06, 0.014, 0.23),
        ("PE", 14_205, 14_500, 12, -0.01, 0.025, 0.45),
    ):
        process = ql.BlackScholesMertonProcess(
            ql.QuoteHandle(ql.SimpleQuote(spot)),
            ql.YieldTermStructureHandle(ql.FlatForward(now, dividend, dc)),
            ql.YieldTermStructureHandle(ql.FlatForward(now, rate, dc)),
            ql.BlackVolTermStructureHandle(ql.BlackConstantVol(now, ql.NullCalendar(), iv, dc)),
        )
        payoff = ql.PlainVanillaPayoff(ql.Option.Call if kind == "CE" else ql.Option.Put, strike)
        contract = ql.VanillaOption(payoff, ql.EuropeanExercise(now + days))
        contract.setPricingEngine(ql.AnalyticEuropeanEngine(process))
        output.append(
            {
                "model": {
                    "spot": spot,
                    "strike": strike,
                    "years": days / 365,
                    "rate": rate,
                    "dividend": dividend,
                    "kind": kind,
                },
                "premium": contract.NPV(),
                "iv": iv,
                "greeks": {
                    "delta": contract.delta(),
                    "gamma_per_paisa": contract.gamma(),
                    "theta_paisa_per_day": contract.thetaPerDay(),
                    "vega_paisa_per_vol_point": contract.vega() / 100,
                    "rho_paisa_per_rate_point": contract.rho() / 100,
                },
            }
        )
    return output


def main() -> None:
    ta, ql, np = (import_module(name) for name in ("talib", "QuantLib", "numpy"))
    if (ta.__version__, ql.__version__) != ("0.6.8", "1.40"):
        raise ValueError("use the pinned reference versions")
    doc = {
        "reference": {"ta_lib": ta.__version__, "quantlib": ql.__version__},
        "sample_indices": SAMPLE_INDICES,
        "technical": {
            pattern.value: technical(np, ta, pattern)
            for pattern in (Pattern.TREND, Pattern.CHOP, Pattern.GAP)
        },
        "options": european(ql),
    }
    GOLDEN.write_text(json.dumps(doc, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
