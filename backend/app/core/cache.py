"""In-process hot cache (ARCHITECTURE §2 law 5: no Redis).

A small interface so callers don't depend on the implementation. `MemoryCache` is a
bounded LRU with per-key TTL measured on the injected clock's monotonic time. It is a
cache, never a source of truth — durable state lives in TimescaleDB.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Protocol

from app.core.clock import Clock


class Cache(Protocol):
    def get(self, key: str) -> object | None: ...
    def set(self, key: str, value: object) -> None: ...
    def setex(self, key: str, ttl_seconds: float, value: object) -> None: ...
    def delete(self, key: str) -> bool: ...


class MemoryCache:
    def __init__(self, clock: Clock, max_entries: int) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._clock = clock
        self._max = max_entries
        self._data: OrderedDict[str, tuple[object, float | None]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> object | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at is not None and self._clock.monotonic() >= expires_at:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: object) -> None:
        self._put(key, value, None)

    def setex(self, key: str, ttl_seconds: float, value: object) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        self._put(key, value, self._clock.monotonic() + ttl_seconds)

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._data.pop(key, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def _put(self, key: str, value: object, expires_at: float | None) -> None:
        if value is None:
            raise ValueError("None is not cacheable (indistinguishable from a miss)")
        with self._lock:
            self._data[key] = (value, expires_at)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)
