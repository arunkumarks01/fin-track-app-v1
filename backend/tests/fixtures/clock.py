"""Frozen clock for deterministic tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class FrozenClock:
    """A `Clock` that only moves when told to."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FrozenClock needs an aware datetime")
        self._now = start.astimezone(UTC)
        self._mono = 1_000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("time only moves forward")
        self._now += timedelta(seconds=seconds)
        self._mono += seconds

    def set(self, when: datetime) -> None:
        when = when.astimezone(UTC)
        if when < self._now:
            raise ValueError("time only moves forward")
        self.advance((when - self._now).total_seconds())
