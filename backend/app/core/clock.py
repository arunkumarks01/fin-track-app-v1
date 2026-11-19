"""Injectable clock — the determinism seam.

Production code asks a `Clock` for time instead of calling `datetime.now()` directly, so
tests can freeze and advance time without monkeypatching (TEST_PLAN: determinism or nothing).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, timezone-aware UTC."""
        ...

    def monotonic(self) -> float:
        """Monotonic seconds, for TTLs and durations."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()
