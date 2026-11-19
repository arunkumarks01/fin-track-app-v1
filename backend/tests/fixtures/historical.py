"""Scripted async historical source (stands in for KiteClient in extractor tests)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.money import paisa_to_rupees
from app.core.tradingdate import MARKET_TZ
from tests.fixtures.candles import Candle


def kite_record(c: Candle) -> list[Any]:
    """A factory candle as Kite sends it: '+0530' timestamp, rupee Decimals."""
    return [
        c.ts.astimezone(MARKET_TZ).strftime("%Y-%m-%dT%H:%M:%S%z"),
        paisa_to_rupees(c.open),
        paisa_to_rupees(c.high),
        paisa_to_rupees(c.low),
        paisa_to_rupees(c.close),
        c.volume,
    ]


def record_ts(record: list[Any]) -> datetime:
    return datetime.fromisoformat(str(record[0]))


@dataclass
class FakeHistorical:
    series: dict[int, list[list[Any]]] = field(default_factory=dict)
    calls: list[tuple[int, str, datetime, datetime]] = field(default_factory=list)
    fail_on_call: dict[int, Exception] = field(default_factory=dict)  # call index -> exc

    async def historical_raw(
        self, instrument_token: int, interval: str, start: datetime, end: datetime
    ) -> list[Any]:
        index = len(self.calls)
        self.calls.append((instrument_token, interval, start, end))
        if index in self.fail_on_call:
            raise self.fail_on_call[index]
        return [r for r in self.series.get(instrument_token, []) if start <= record_ts(r) <= end]
