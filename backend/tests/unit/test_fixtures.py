"""The test seams themselves must be deterministic and honest."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.tradingdate import canonical_daily_ts, trading_date_of
from tests.fixtures.candles import Pattern, make_daily_candles
from tests.fixtures.clock import FrozenClock
from tests.fixtures.kite import FakeKite, FakeKiteError
from tests.fixtures.llm import FakeLLM, FakeLLMError, FakeLLMTimeout, LLMMode

IST = ZoneInfo("Asia/Kolkata")


# --- frozen clock ---
def test_frozen_clock_does_not_move(frozen_clock: FrozenClock) -> None:
    assert frozen_clock.now() == frozen_clock.now()
    assert frozen_clock.now().tzinfo is UTC


def test_frozen_clock_advances_and_refuses_rewind(frozen_clock: FrozenClock) -> None:
    t0, m0 = frozen_clock.now(), frozen_clock.monotonic()
    frozen_clock.advance(90)
    assert frozen_clock.now() - t0 == timedelta(seconds=90)
    assert frozen_clock.monotonic() - m0 == 90
    with pytest.raises(ValueError, match="forward"):
        frozen_clock.set(t0)


# --- trading-date identity ---
def test_canonical_daily_ts_is_ist_midnight() -> None:
    ts = canonical_daily_ts(date(2024, 3, 1), IST)
    assert ts.astimezone(IST).time() == time(0, 0)
    assert ts.astimezone(UTC) == datetime(2024, 2, 29, 18, 30, tzinfo=UTC)


def test_trading_date_of_late_utc_is_next_ist_day() -> None:
    # 20:00 UTC = 01:30 IST next day
    assert trading_date_of(datetime(2024, 3, 1, 20, 0, tzinfo=UTC), IST) == date(2024, 3, 2)
    with pytest.raises(ValueError, match="naive"):
        trading_date_of(datetime(2024, 3, 1, 20, 0), IST)  # noqa: DTZ001


# --- candle factory ---
@pytest.mark.parametrize("pattern", list(Pattern))
def test_factory_is_deterministic(pattern: Pattern) -> None:
    assert make_daily_candles(pattern, seed=3) == make_daily_candles(pattern, seed=3)


@pytest.mark.parametrize("pattern", [p for p in Pattern if p is not Pattern.BAD_BAR])
def test_factory_bars_well_formed_paisa(pattern: Pattern) -> None:
    candles = make_daily_candles(pattern, n=80)
    assert len(candles) == 80
    for c in candles:
        assert c.is_well_formed()
        assert all(isinstance(v, int) for v in (c.open, c.high, c.low, c.close, c.volume))
        assert c.ts.astimezone(IST).time() == time(0, 0)
        assert c.ts.astimezone(IST).weekday() < 5
    assert len({c.ts for c in candles}) == 80  # one bar per trading date


def test_factory_trend_rises_and_split_halves() -> None:
    trend = make_daily_candles(Pattern.TREND, n=40)
    assert trend[-1].close > trend[0].close
    split = make_daily_candles(Pattern.SPLIT, n=40, split_ratio=2)
    before, after = split[19].close, split[20].open
    assert after == before // 2


def test_factory_bad_bar_has_exactly_one_malformed() -> None:
    candles = make_daily_candles(Pattern.BAD_BAR, n=30)
    assert [c.is_well_formed() for c in candles].count(False) == 1


@given(st.integers(min_value=0, max_value=10_000), st.integers(min_value=100, max_value=10**8))
def test_factory_property_ohlc_order(seed: int, start_close: int) -> None:
    for c in make_daily_candles(Pattern.CHOP, n=10, seed=seed, start_close=start_close):
        assert c.is_well_formed()


# --- mock kite ---
def test_fake_kite_filters_range_and_records_calls(fake_kite: FakeKite) -> None:
    d = datetime(2024, 1, 1, tzinfo=IST)
    fake_kite.historical[(256265, "day")] = [
        {"date": d + timedelta(days=i), "close": 100 + i} for i in range(5)
    ]
    rows = fake_kite.historical_data(256265, d + timedelta(days=1), d + timedelta(days=3), "day")
    assert [r["close"] for r in rows] == [101, 102, 103]
    assert fake_kite.calls[0][0] == "historical_data"


def test_fake_kite_failure_injection(fake_kite: FakeKite) -> None:
    fake_kite.fail_with = FakeKiteError("rate limited")
    with pytest.raises(FakeKiteError):
        fake_kite.quote(["NSE:INFY"])
    assert fake_kite.calls == [("quote", (("NSE:INFY",),))]


def test_fake_kite_ltp(fake_kite: FakeKite) -> None:
    fake_kite.quotes["NSE:INFY"] = {"last_price": 150_000, "volume": 10}
    assert fake_kite.ltp(["NSE:INFY", "NSE:NONE"]) == {"NSE:INFY": {"last_price": 150_000}}


# --- mock llm ---
def test_fake_llm_ok(fake_llm: FakeLLM) -> None:
    fake_llm.response = {"sentiment": 62, "material_event": False}
    assert json.loads(fake_llm.complete("classify")) == fake_llm.response
    assert fake_llm.prompts == ["classify"]


@pytest.mark.parametrize(
    ("mode", "exc"), [(LLMMode.TIMEOUT, FakeLLMTimeout), (LLMMode.ERROR, FakeLLMError)]
)
def test_fake_llm_raising_modes(fake_llm: FakeLLM, mode: LLMMode, exc: type[Exception]) -> None:
    fake_llm.mode = mode
    with pytest.raises(exc):
        fake_llm.complete("x")


def test_fake_llm_malformed_and_schema_mismatch(fake_llm: FakeLLM) -> None:
    fake_llm.mode = LLMMode.MALFORMED
    with pytest.raises(json.JSONDecodeError):
        json.loads(fake_llm.complete("x"))
    fake_llm.mode = LLMMode.SCHEMA_MISMATCH
    assert "sentiment" not in json.loads(fake_llm.complete("x"))
