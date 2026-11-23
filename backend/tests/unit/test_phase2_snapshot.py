"""One deterministic research snapshot covering every Phase 2 output field."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from app.analytics.indicators import Bar
from app.analytics.options import Contract, Model, OptionType, expiry_analytics
from app.analytics.technical import Calendar, technical_series
from app.broker.kite.client import parse_candle
from app.core.tradingdate import MARKET_TZ
from tests.conftest import build_settings
from tests.fixtures.candles import Pattern
from tests.fixtures.minutes import golden
from tests.unit.test_phase2_analytics import bars_for, stamp

_SNAPSHOT = Path(__file__).parents[1] / "fixtures" / "analytics_regression.json"


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 10)
    if isinstance(value, dict):
        return {key: _rounded(element) for key, element in value.items()}
    if isinstance(value, list):
        return [_rounded(element) for element in value]
    return value


def _snapshot() -> dict[str, Any]:
    settings = build_settings()
    daily = bars_for(Pattern.GAP, 260)
    by_day = {datetime.fromtimestamp(b.t, MARKET_TZ).date(): b for b in daily}
    calendar = Calendar(list(by_day), {}, {}, frozenset())
    benchmark = {b.t: 3_000_000 + i * 18 for i, b in enumerate(daily)}
    daily_rows = technical_series(daily, calendar, by_day, benchmark, None, settings)
    expiry = date(2026, 12, 22)
    contracts = []
    prior = {}
    kinds: tuple[OptionType, ...] = ("CE", "PE")
    for kind in kinds:
        for i, strike in enumerate((99_000, 100_000, 101_000)):
            model = Model(100_000, strike, 30 / 365, 0.06, 0.01, kind)
            premium = round(model.price(0.25))
            contract = Contract(expiry, strike, kind, premium - 1, premium + 1, 3, 4, 100 + i, 50)
            previous = Contract(expiry, strike, kind, premium - 3, premium - 1, 3, 4, 95 + i, 20)
            contracts.append(contract)
            prior[previous.key] = previous
    [case] = [
        item
        for item in golden()["cases"]
        if item["symbol"] == "NSE:RELIANCE" and item["date"] == "2026-11-18"
    ]
    day = date.fromisoformat(case["date"])
    minute = [
        Bar(int(b.ts.timestamp()), b.open, b.high, b.low, b.close, b.volume)
        for b in map(parse_candle, case["bars"]["minute"])
    ]
    intraday_rows = technical_series(
        minute,
        Calendar([day], {day: stamp(day, 9, 15)}, {day: "complete"}, frozenset()),
        {},
        {},
        1,
        settings,
    )
    return {
        "daily_warmup": daily_rows[198],
        "daily_ready": daily_rows[-1],
        "recorded_intraday": intraday_rows[-1],
        "option_expiry": expiry_analytics(
            contracts, prior, 100_000, 30 / 365, (0.06, 0.01), 5, True
        ),
    }


def test_phase2_regression_snapshot(pytestconfig: pytest.Config) -> None:
    actual = _rounded(_snapshot())
    if pytestconfig.getoption("--snapshot-update"):
        payload = json.dumps(actual, indent=2, sort_keys=True, allow_nan=False) + "\n"
        _SNAPSHOT.write_text(payload, encoding="utf-8")
    assert actual == json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
