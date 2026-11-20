"""When is a bar final? 1d: next morning (Kite publishes the official close overnight).
1m: the same evening. Both skip weekends; holidays simply yield no bars."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app.core.tradingdate import MARKET_TZ
from app.data.extract_daily import last_completed_session, last_final_daily_session

NEXT_DAY = time(8, 15)


def ist(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=MARKET_TZ)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # Thu 2026-11-19's bar fetched Thu evening was provisional (the 1a bug) — not final yet
        (ist(2026, 11, 19, 18, 25), date(2026, 11, 18)),
        # Fri before 08:15: Thu still not final
        (ist(2026, 11, 20, 8, 14), date(2026, 11, 18)),
        # Fri at/after 08:15: Thu is final
        (ist(2026, 11, 20, 8, 15), date(2026, 11, 19)),
        (ist(2026, 11, 20, 16, 15), date(2026, 11, 19)),  # Fri's own bar is NOT final today
        # Sat morning: Fri becomes final
        (ist(2026, 11, 21, 8, 30), date(2026, 11, 20)),
        # Mon 08:30: weekend skipped back to Fri
        (ist(2026, 11, 23, 8, 30), date(2026, 11, 20)),
        # Mon 08:00 (before finality): still Fri — Sun/Sat are not sessions
        (ist(2026, 11, 23, 8, 0), date(2026, 11, 20)),
        # Tue 08:15: Mon final
        (ist(2026, 11, 24, 8, 15), date(2026, 11, 23)),
    ],
)
def test_daily_bar_is_final_only_the_next_morning(now: datetime, expected: date) -> None:
    assert last_final_daily_session(now, NEXT_DAY) == expected


def test_daily_finality_is_timezone_safe() -> None:
    # 02:45 UTC Friday == 08:15 IST Friday → Thursday final
    from datetime import UTC

    assert last_final_daily_session(datetime(2026, 11, 20, 2, 45, tzinfo=UTC), NEXT_DAY) == date(
        2026, 11, 19
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (ist(2026, 11, 19, 15, 59), date(2026, 11, 18)),
        (ist(2026, 11, 19, 16, 0), date(2026, 11, 19)),  # 1m: same evening is final
        (ist(2026, 11, 22, 12, 0), date(2026, 11, 20)),  # Sunday → Friday
    ],
)
def test_intraday_bars_are_final_the_same_evening(now: datetime, expected: date) -> None:
    assert last_completed_session(now, time(16, 0)) == expected
