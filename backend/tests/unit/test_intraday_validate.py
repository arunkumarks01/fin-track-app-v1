from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.core.tradingdate import MARKET_TZ
from app.data.intraday import validate_minute
from tests.fixtures.minutes import golden, session

IST = MARKET_TZ


def test_clean_session_passes_sorted_exact_paisa() -> None:
    recs = session(date(2026, 11, 18))
    v = validate_minute("NSE:T", 7, list(reversed(recs)))
    assert v.rejected == []
    assert len(v.clean) == 375
    assert v.clean[0].ts == datetime(2026, 11, 18, 9, 15, tzinfo=IST)
    assert all(b.sid == 7 for b in v.clean)
    assert [b.ts for b in v.clean] == sorted(b.ts for b in v.clean)


def test_real_kite_minutes_pass() -> None:
    for case in golden()["cases"]:
        bars = [
            [r[0], *(Decimal(x) if isinstance(x, str) else x for x in r[1:])]
            for r in case["bars"]["minute"]
        ]
        v = validate_minute(case["symbol"], 1, bars)
        assert v.rejected == [], case["symbol"]
        assert len(v.clean) == len(bars)


@pytest.mark.parametrize(
    ("record", "reason"),
    [
        (["2026-11-18T09:15:30+0530", 10, 11, 9, 10, 5], "non_canonical_ts"),
        (["2026-11-18T09:15:00+0530", 0, 11, 9, 10, 5], "non_positive_price"),
        (["2026-11-18T09:15:00+0530", 10, 9, 9, 10, 5], "ohlc_order"),
        (["2026-11-18T09:15:00+0530", 10, 11, 9, 10, -1], "negative_volume"),
        # real Kite payload (NSE:COROMANDEL 2015-03-13 09:15): 2**64-2, an unsigned underflow
        (["2026-11-18T09:15:00+0530", 10, 11, 9, 10, 18446744073709551614], "out_of_range"),
        (
            [
                "2026-11-18T09:15:00+0530",
                Decimal("30000000"),
                Decimal("30000000"),
                Decimal("30000000"),
                Decimal("30000000"),
                1,
            ],
            "out_of_range",
        ),
        (["2026-11-18T09:15:00+0530", Decimal("1.001"), 11, 9, 10, 5], "unparseable"),
    ],
)
def test_rejections(record: list[object], reason: str) -> None:
    v = validate_minute("NSE:T", 1, [record])
    assert [r.reason for r in v.rejected] == [reason]
    assert v.clean == []


def test_duplicate_minute_quarantines_every_copy() -> None:
    recs = session(date(2026, 11, 18), minutes=3)
    v = validate_minute("NSE:T", 1, [*recs, recs[1]])
    assert [r.reason for r in v.rejected] == ["duplicate_in_batch", "duplicate_in_batch"]
    assert len(v.clean) == 2
