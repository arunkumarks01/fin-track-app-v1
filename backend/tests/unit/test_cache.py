from __future__ import annotations

import pytest

from app.core.cache import Cache, MemoryCache
from tests.fixtures.clock import FrozenClock


@pytest.fixture
def cache(frozen_clock: FrozenClock) -> MemoryCache:
    return MemoryCache(frozen_clock, max_entries=3)


def test_satisfies_interface(cache: MemoryCache) -> None:
    c: Cache = cache
    assert c.get("missing") is None


def test_set_get_delete(cache: MemoryCache) -> None:
    cache.set("k", {"ltp": 123_450})
    assert cache.get("k") == {"ltp": 123_450}
    assert cache.delete("k") is True
    assert cache.get("k") is None
    assert cache.delete("k") is False


def test_setex_expires_on_frozen_clock(cache: MemoryCache, frozen_clock: FrozenClock) -> None:
    cache.setex("q", 5, "quote")
    frozen_clock.advance(4.999)
    assert cache.get("q") == "quote"
    frozen_clock.advance(0.001)
    assert cache.get("q") is None
    assert len(cache) == 0


def test_set_clears_previous_ttl(cache: MemoryCache, frozen_clock: FrozenClock) -> None:
    cache.setex("k", 1, "a")
    cache.set("k", "b")
    frozen_clock.advance(10)
    assert cache.get("k") == "b"


def test_lru_bound(cache: MemoryCache) -> None:
    for k in ("a", "b", "c"):
        cache.set(k, k)
    assert cache.get("a") == "a"  # touch a -> b is now oldest
    cache.set("d", "d")
    assert cache.get("b") is None
    assert [cache.get(k) for k in ("a", "c", "d")] == ["a", "c", "d"]


@pytest.mark.parametrize("ttl", [0, -1])
def test_non_positive_ttl_rejected(cache: MemoryCache, ttl: float) -> None:
    with pytest.raises(ValueError, match="ttl"):
        cache.setex("k", ttl, "v")


def test_none_not_cacheable(cache: MemoryCache) -> None:
    with pytest.raises(ValueError, match="None"):
        cache.set("k", None)


def test_invalid_bound(frozen_clock: FrozenClock) -> None:
    with pytest.raises(ValueError, match="max_entries"):
        MemoryCache(frozen_clock, max_entries=0)
