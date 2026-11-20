"""Deterministic 1-minute session factory + Kite-format records, and the real Kite golden data."""

from __future__ import annotations

import json
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from app.core.money import paisa_to_rupees
from app.core.tradingdate import MARKET_TZ

GOLDEN = Path(__file__).parent / "kite_golden" / "intraday_2days.json"


def kite_minute(ts: datetime, o: int, h: int, lo: int, c: int, v: int) -> list[Any]:
    return [
        ts.astimezone(MARKET_TZ).strftime("%Y-%m-%dT%H:%M:%S%z"),
        paisa_to_rupees(o),
        paisa_to_rupees(h),
        paisa_to_rupees(lo),
        paisa_to_rupees(c),
        v,
    ]


def session(
    day: date,
    *,
    start: time = time(9, 15),
    minutes: int = 375,
    price: int = 100_000,
    seed: int = 1,
    skip: frozenset[int] = frozenset(),
) -> list[list[Any]]:
    """One session of Kite-format 1m records; `skip` = minute indexes that are missing."""
    rng = random.Random(seed * 1_000_003 + day.toordinal())  # noqa: S311 — test data
    t0 = datetime.combine(day, start, MARKET_TZ)
    out, last = [], price
    for i in range(minutes):
        o = last
        c = max(1, o + rng.randint(-50, 50))
        h, lo = max(o, c) + rng.randint(0, 20), max(1, min(o, c) - rng.randint(0, 20))
        if i not in skip:
            out.append(kite_minute(t0 + timedelta(minutes=i), o, h, lo, c, rng.randint(0, 900)))
        last = c
    return out


def weekdays(start: date, end: date) -> list[date]:
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def golden() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(GOLDEN.read_text())
    return data
