from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.money import paisa_to_rupees
from app.core.tradingdate import MARKET_TZ
from app.data.validate import PrevBar, Validated, validate_daily, weekdays_between
from tests.fixtures.candles import Candle, Pattern, make_daily_candles

IST = MARKET_TZ


def check(records: list[Any], prev: PrevBar | None = None, *, is_index: bool = False) -> Validated:
    return validate_daily(
        "NSE:T",
        records,
        prev=prev,
        large_gap_pct=20,
        missing_weekdays_flag=3,
        is_index=is_index,
        index_max_gap_pct=20,
    )


def kite_record(c: Candle) -> list[Any]:
    """A factory candle as Kite would send it (rupee Decimals, +0530 timestamp)."""
    return [
        c.ts.astimezone(IST).strftime("%Y-%m-%dT%H:%M:%S%z"),
        paisa_to_rupees(c.open),
        paisa_to_rupees(c.high),
        paisa_to_rupees(c.low),
        paisa_to_rupees(c.close),
        c.volume,
    ]


def test_clean_trend_passes_untouched() -> None:
    candles = make_daily_candles(Pattern.TREND, n=30)
    v = check([kite_record(c) for c in candles])
    assert v.rejected == []
    assert v.flags == []
    assert [(b.ts, b.open, b.high, b.low, b.close, b.volume) for b in v.clean] == [
        (c.ts, c.open, c.high, c.low, c.close, c.volume) for c in candles
    ]


def test_bad_bar_quarantined_rest_kept() -> None:
    candles = make_daily_candles(Pattern.BAD_BAR, n=20)
    v = check([kite_record(c) for c in candles])
    assert [r.reason for r in v.rejected] == ["ohlc_order"]
    assert len(v.clean) == 19


def test_split_kept_and_flagged_as_large_gap() -> None:
    candles = make_daily_candles(Pattern.SPLIT, n=20, split_ratio=2)
    v = check([kite_record(c) for c in candles])
    assert v.rejected == []
    assert len(v.clean) == 20  # flag-but-keep, never deleted
    assert [(f.flag, f.ts) for f in v.flags] == [("large_gap", candles[10].ts)]


def test_gap_up_below_threshold_not_flagged() -> None:
    candles = make_daily_candles(Pattern.GAP, n=20)  # +8% < 20%
    v = check([kite_record(c) for c in candles])
    assert v.flags == []


def test_duplicate_trading_date_quarantines_every_copy() -> None:
    c = make_daily_candles(Pattern.TREND, n=3)
    records = [kite_record(x) for x in c] + [kite_record(c[1])]
    v = check(records)
    assert [r.reason for r in v.rejected] == ["duplicate_in_batch", "duplicate_in_batch"]
    assert [b.ts for b in v.clean] == [c[0].ts, c[2].ts]


@pytest.mark.parametrize(
    ("record", "reason"),
    [
        (["2024-01-01T18:30:00+0000", 10, 11, 9, 10, 5], None),  # == 00:00 IST -> canonical
        (["2024-01-02T00:00:00+0530", 0, 11, 9, 10, 5], "non_positive_price"),
        (["2024-01-02T00:00:00+0530", 10, 11, 9, 10, -1], "negative_volume"),
        (["2024-01-02T00:00:00+0530", Decimal("1.001"), 11, 9, 10, 5], "unparseable"),
        (["2024-01-02T00:00:00+0530", 10, 11], "unparseable"),
    ],
)
def test_rejection_reasons(record: list[Any], reason: str | None) -> None:
    v = check([record])
    assert [r.reason for r in v.rejected] == ([reason] if reason else [])
    assert len(v.clean) == (0 if reason else 1)


def test_prev_bar_from_db_drives_gap_checks() -> None:
    prev = PrevBar(datetime(2024, 1, 1, tzinfo=IST), close=1000)
    # next bar 8 weekdays later, opening +50%
    v = check([["2024-01-12T00:00:00+0530", 15, 16, 14, 15, 5]], prev)
    assert {f.flag for f in v.flags} == {"large_gap", "missing_sessions"}
    assert len(v.clean) == 1


def test_weekdays_between() -> None:
    assert weekdays_between(date(2024, 1, 5), date(2024, 1, 8)) == 0  # Fri -> Mon
    assert weekdays_between(date(2024, 1, 1), date(2024, 1, 12)) == 8
    assert weekdays_between(date(2024, 1, 8), date(2024, 1, 8)) == 0


@given(st.integers(0, 5000), st.sampled_from([Pattern.TREND, Pattern.CHOP, Pattern.GAP]))
def test_property_well_formed_series_never_rejected(seed: int, pattern: Pattern) -> None:
    candles = make_daily_candles(pattern, n=15, seed=seed)
    v = check([kite_record(c) for c in candles])
    assert v.rejected == []
    assert len(v.clean) == 15


# --- rules learned from the first real Kite backfill ---
def test_legacy_0915_stamp_is_restamped_and_flagged() -> None:
    v = check([["2018-11-30T09:15:00+0530", 100, 110, 95, 105, 0]])
    assert v.rejected == []
    assert [b.ts for b in v.clean] == [datetime(2018, 11, 30, tzinfo=IST)]
    assert [(f.flag, f.ts) for f in v.flags] == [("retimed_ts", datetime(2018, 11, 30, tzinfo=IST))]
    assert "2018-11-30T09:15:00+05:30" in v.flags[0].detail  # original ts kept for audit


def test_native_midnight_bar_wins_over_restamped_copy() -> None:
    # the real BANKEX 2015-11-09 case: a junk 07:16 bar next to the genuine 00:00 bar
    v = check(
        [
            ["2015-11-09T00:00:00+0530", 20000, 20100, 19900, 20050, 0],
            [
                "2015-11-09T07:16:46+0530",
                Decimal("92.97"),
                Decimal("92.97"),
                Decimal("92.97"),
                Decimal("92.97"),
                0,
            ],
        ]
    )
    assert [b.close for b in v.clean] == [2_005_000]
    assert [r.reason for r in v.rejected] == ["duplicate_in_batch"]
    assert "lost to native 00:00 bar" in v.rejected[0].detail
    assert v.flags == []


def test_two_restamped_copies_quarantine_both() -> None:
    v = check(
        [
            ["2018-01-02T09:15:00+0530", 100, 110, 95, 105, 0],
            ["2018-01-02T08:14:19+0530", 101, 111, 96, 106, 0],
        ]
    )
    assert v.clean == []
    assert [r.reason for r in v.rejected] == ["duplicate_in_batch", "duplicate_in_batch"]


def test_index_gap_quarantined_reference_stays_on_good_bar() -> None:
    prev = PrevBar(datetime(2024, 1, 1, tzinfo=IST), close=2_000_000)
    v = check(
        [
            ["2024-01-02T00:00:00+0530", 9297, 9297, 9297, 9297, 0],  # 92.97 junk
            ["2024-01-03T00:00:00+0530", 20010, 20100, 19950, 20050, 0],  # back to normal
        ],
        prev,
        is_index=True,
    )
    assert [r.reason for r in v.rejected] == ["index_gap"]
    assert [b.ts.day for b in v.clean] == [3]
    assert v.flags == []  # the recovery bar is compared to the last *good* close


def test_same_jump_on_equity_is_flag_but_keep() -> None:
    prev = PrevBar(datetime(2024, 1, 1, tzinfo=IST), close=200_000)
    v = check([["2024-01-02T00:00:00+0530", 1000, 1010, 990, 1005, 5]], prev)
    assert v.rejected == []
    assert [f.flag for f in v.flags] == ["large_gap"]
