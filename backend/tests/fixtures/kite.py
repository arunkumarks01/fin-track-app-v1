"""Mock Kite — no network, scripted responses, call recording.

Mirrors the `kiteconnect.KiteConnect` method names the data foundation will use
(`historical_data`, `quote`, `ltp`) so Phase-1 code can be tested against it unchanged.
Failure injection lets tests assert fail-closed behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class FakeKiteError(Exception):
    """Raised when a scripted failure is injected."""


@dataclass
class FakeKite:
    historical: dict[tuple[int, str], list[dict[str, Any]]] = field(default_factory=dict)
    quotes: dict[str, dict[str, Any]] = field(default_factory=dict)
    fail_with: Exception | None = None
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if self.fail_with is not None:
            raise self.fail_with

    def historical_data(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str,
    ) -> list[dict[str, Any]]:
        self._record("historical_data", instrument_token, from_date, to_date, interval)
        rows = self.historical.get((instrument_token, interval), [])
        return [dict(r) for r in rows if from_date <= r["date"] <= to_date]

    def quote(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        self._record("quote", tuple(instruments))
        return {i: dict(self.quotes[i]) for i in instruments if i in self.quotes}

    def ltp(self, instruments: list[str]) -> dict[str, dict[str, Any]]:
        self._record("ltp", tuple(instruments))
        return {
            i: {"last_price": self.quotes[i]["last_price"]} for i in instruments if i in self.quotes
        }
