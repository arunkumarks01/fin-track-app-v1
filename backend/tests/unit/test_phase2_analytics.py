"""Independent numerical goldens and deterministic Phase 2 regression checks."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from math import isclose, sqrt
from typing import Any

import pytest

from app.analytics.chain import years_to_expiry
from app.analytics.indicators import Bar, calculate, ema
from app.analytics.options import (
    Contract,
    Model,
    constant_tenor_iv,
    expiry_analytics,
    greeks,
    implied_volatility,
    iv_rank,
)
from app.analytics.parameters import IndicatorPeriods
from app.analytics.technical import Calendar, technical_series
from app.core.tradingdate import MARKET_TZ
from tests.conftest import build_settings
from tests.fixtures.candles import Pattern, make_daily_candles
from tests.fixtures.generate_analytics_golden import BAR_COUNT, GOLDEN


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    value: dict[str, Any] = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return value


def bars_for(pattern: Pattern, count: int) -> list[Bar]:
    return [
        Bar(int(b.ts.timestamp()), b.open, b.high, b.low, b.close, b.volume)
        for b in make_daily_candles(pattern, n=count)
    ]


@pytest.mark.parametrize("pattern", [Pattern.TREND, Pattern.CHOP, Pattern.GAP])
def test_technical_indicators_match_ta_lib(pattern: Pattern, golden: dict[str, Any]) -> None:
    actual = calculate(bars_for(pattern, BAR_COUNT), IndicatorPeriods())
    for name, expected in golden["technical"][pattern.value].items():
        observed = [actual[name][i] for i in golden["sample_indices"]]
        for index, actual_value, expected_value in zip(
            golden["sample_indices"], observed, expected, strict=True
        ):
            if expected_value is None:
                assert actual_value is None, (name, index)
            else:
                assert actual_value is not None, (name, index)
                assert isclose(actual_value, expected_value, rel_tol=1e-9, abs_tol=1e-7), (
                    name,
                    index,
                    actual_value,
                    expected_value,
                )


@pytest.mark.parametrize("pattern", [Pattern.TREND, Pattern.CHOP, Pattern.GAP])
def test_ema200_minimum_and_warmup_convergence(pattern: Pattern) -> None:
    prices = [b.close for b in bars_for(pattern, 1300)]
    assert ema(prices[-199:], 200)[-1] is None
    assert ema(prices[-200:], 200)[-1] == sum(prices[-200:]) / 200
    full, warmed = ema(prices, 200)[-1], ema(prices[-800:], 200)[-1]
    assert full is not None
    assert warmed is not None
    assert isclose(full, warmed, rel_tol=0.0001)


@pytest.mark.parametrize("size", [0, 1, 2, 13, 14, 26, 27, 33, 199, 200, 203])
def test_all_indicator_series_are_aligned_and_causal(size: int) -> None:
    bars = bars_for(Pattern.CHOP, 260)
    complete = calculate(bars, IndicatorPeriods())
    short = calculate(bars[:size], IndicatorPeriods())
    assert short == {key: curve[:size] for key, curve in complete.items()}


def test_bad_or_duplicate_bars_are_rejected() -> None:
    first, second = bars_for(Pattern.TREND, 2)
    with pytest.raises(ValueError, match="strictly increasing"):
        calculate([first, first, second], IndicatorPeriods())
    with pytest.raises(ValueError, match="invalid OHLCV"):
        calculate([Bar(first.t, 100, 80, 90, 95, 10)], IndicatorPeriods())


def stamp(d: date, hour: int = 0, minute: int = 0) -> int:
    return int(datetime.combine(d, time(hour, minute), MARKET_TZ).timestamp())


def test_session_analytics_regression_snapshot() -> None:
    prior, current = date(2026, 11, 19), date(2026, 11, 20)
    day = Bar(stamp(prior), 105, 130, 100, 124, 90)
    opening = stamp(current, 9, 15)
    bars = [
        Bar(opening, 130, 145, 125, 135, 3),
        Bar(opening + 300, 135, 142, 133, 140, 6),
        Bar(opening + 600, 140, 143, 134, 138, 3),
    ]
    calendar = Calendar([prior, current], {current: opening}, {current: "complete"}, frozenset())
    cfg = build_settings(analytics_periods={"relative_strength": 2})
    benchmark = {b.t: 200 + i for i, b in enumerate(bars)}
    rows = technical_series(bars, calendar, {prior: day}, benchmark, 5, cfg)
    values = rows[-1]["values"]
    expected = {
        "pivot_paisa": 118,
        "cpr_top_paisa": 121,
        "cpr_bottom_paisa": 115,
        "opening_range_high_paisa": 145,
        "opening_range_low_paisa": 125,
        "vwap_paisa": 138,
        "ema_200_paisa": None,
    }
    assert {k: values[k] for k in expected} == expected
    assert values["relative_strength_pct"] == pytest.approx(100 * (138 * 200 / (135 * 202) - 1))
    assert values["gap_pct"] == pytest.approx(100 * (130 / 124 - 1))
    assert rows[1]["values"]["opening_range_high_paisa"] is None
    assert (
        technical_series(bars[1:], calendar, {prior: day}, benchmark, 5, cfg)[-1]["values"][
            "vwap_paisa"
        ]
        is None
    )
    gapped = technical_series([bars[0], bars[2]], calendar, {prior: day}, benchmark, 5, cfg)
    assert [row["status"] for row in gapped] == ["intraday_candle_gap"] * 2
    short_calendar = Calendar([prior, current], {current: opening}, {current: "short"}, frozenset())
    assert technical_series(bars, short_calendar, {}, {}, 5, cfg)[-1]["values"] is None


def test_missing_session_resets_warmup_unless_verified_no_trade() -> None:
    base = date(2026, 11, 16)
    dates = [base + timedelta(days=i) for i in range(5)]
    bars = [Bar(stamp(d), 100, 105, 95, 100, 3) for d in dates if d != dates[2]]
    cfg = build_settings()
    for no_trade, expected in [(frozenset(), 2), (frozenset({dates[2]}), 4)]:
        calendar = Calendar(dates, {}, {}, no_trade)
        assert technical_series(bars, calendar, {}, {}, None, cfg)[-1]["usable_bars"] == expected


def test_options_price_iv_and_greeks_match_quantlib(golden: dict[str, Any]) -> None:
    for row in golden["options"]:
        model = Model(**row["model"])
        iv = implied_volatility(model, row["premium"], 5.0)
        assert iv == pytest.approx(row["iv"])
        assert model.price(row["iv"]) == pytest.approx(row["premium"])
        assert asdict(greeks(model, row["iv"])) == pytest.approx(row["greeks"], rel=1e-9)


def test_options_regression_paired_strikes_oi_buildup_and_missing_fields() -> None:
    expiry = date(2026, 12, 29)
    calls = [
        Contract(expiry, k, "CE", 5, 7, 1, 1, oi, volume)
        for k, oi, volume in [(90, 10, 3), (100, 20, 5), (110, 30, 2)]
    ]
    puts = [
        Contract(expiry, k, "PE", 4, 6, 1, 1, oi, volume)
        for k, oi, volume in [(90, 30, 1), (100, 20, 3), (110, 10, 1)]
    ]
    previous = Contract(expiry, 90, "CE", 4, 5, 1, 1, 7, 2)
    full = expiry_analytics(calls + puts, {previous.key: previous}, 100, 0.2, None, 5, True)
    assert full["pcr_oi"] == 1
    assert full["pcr_volume"] == 0.5
    assert full["max_pain_strikes_paisa"] == [100]
    assert full["minimum_payout_paisa"] == 200
    assert full["gamma_wall"] is None
    first = full["contracts"][0]
    assert first["oi_change"] == 3
    assert first["oi_premium_pattern"] == "premium_up_oi_up"
    assert first["midpoint_twice_paisa"] == 12
    assert first["unavailable_reason"] == "pricing_inputs_required"
    unpaired = expiry_analytics(calls + puts[:-1], {}, 100, 0.2, None, 5, True)
    assert unpaired["max_pain_strikes_paisa"] is None
    assert unpaired["pcr_oi"] is None
    zero = Contract(expiry, 100, "CE", 6, 5, 1, 1, 0, 0)
    report = expiry_analytics([zero, puts[1]], {}, 100, 0.2, (0, 0), 5, True)
    assert report["pcr_oi"] is None
    assert report["contracts"][0]["unavailable_reason"] == "missing_or_crossed_quote"


def test_options_expiry_and_no_extrapolation() -> None:
    expiry = date(2026, 12, 8)
    at = datetime.combine(expiry, time(15, 35), MARKET_TZ)
    assert years_to_expiry(expiry, at, True) < 0 < years_to_expiry(expiry, at, False)
    assert implied_volatility(Model(120, 100, 0.2, 0, 0, "CE"), 20, 5) is None
    assert implied_volatility(Model(120, 100, 0.2, 0, 0, "CE"), 120, 5) is None
    assert constant_tenor_iv({15: 0.2, 45: 0.4}, 30) == pytest.approx(sqrt(0.13))
    assert constant_tenor_iv({15: 0.2, 20: 0.4}, 30) is None
    assert iv_rank(0.3, [0.2, 0.6], 3) == pytest.approx(25)
    assert iv_rank(0.3, [0.3, 0.3], 3) is None
    assert iv_rank(0.3, [0.2, 0.6], 4) is None
