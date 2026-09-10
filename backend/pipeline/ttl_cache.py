"""
TTL_CACHE - a bounded, expiring dict.

Three modules had hand-rolled the same `{key: (timestamp, value)}` pattern, and
two of them got it subtly wrong: the MCA cache never expired and never bounded,
which on the always-on box ROADMAP_V3 phase 1 calls for means it grows for the
life of the process and serves a stale CIN forever. This is that pattern once,
with the two things the hand-rolled versions were missing.

Not `functools.lru_cache`: these callers are async (lru_cache would cache the
coroutine, not the result) and they need entries to go stale on a clock, which
lru_cache has no concept of.

Self-check:  python -m backend.pipeline.ttl_cache
"""
from __future__ import annotations

import time
from typing import Any


class TTLCache:
    """Insertion-ordered, size-bounded, time-expiring cache.

    `maxsize` matters as much as `ttl` here: an unbounded lookup cache keyed by
    company name grows without limit on a long-running server, and every entry
    is a dict of scraped fields. When full, the oldest entry is dropped.
    """

    def __init__(self, ttl: float, maxsize: int = 512) -> None:
        self.ttl = ttl
        self.maxsize = maxsize
        self._data: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any, default: Any = None) -> Any:
        hit = self._data.get(key)
        if hit is None:
            return default
        if time.time() - hit[0] >= self.ttl:
            # Drop on read rather than sweeping: these caches are small and read
            # far more often than they are written.
            del self._data[key]
            return default
        return hit[1]

    def set(self, key: Any, value: Any) -> None:
        if key in self._data:
            del self._data[key]          # re-insert so eviction order is by age
        elif len(self._data) >= self.maxsize:
            oldest = next(iter(self._data))
            del self._data[oldest]
        self._data[key] = (time.time(), value)

    def __contains__(self, key: Any) -> bool:
        return self.get(key, _MISS) is not _MISS

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        self._data.clear()


_MISS = object()


def _demo() -> None:
    c = TTLCache(ttl=60, maxsize=3)
    c.set("a", 1)
    assert c.get("a") == 1 and "a" in c
    assert c.get("missing") is None and "missing" not in c

    # Eviction is by age, oldest first, and only when full.
    c.set("b", 2); c.set("c", 3); c.set("d", 4)
    assert len(c) == 3, len(c)
    assert c.get("a") is None, "oldest entry should have been evicted"
    assert c.get("d") == 4

    # Re-setting an existing key must refresh its position, not add a second one.
    c.set("b", 22)
    assert len(c) == 3 and c.get("b") == 22

    # Expiry: a zero TTL means every read is a miss.
    z = TTLCache(ttl=0)
    z.set("k", "v")
    assert z.get("k") is None and "k" not in z
    assert len(z) == 0, "an expired entry must be dropped, not just hidden"

    # A cached falsy value is still a hit, not a miss.
    f = TTLCache(ttl=60)
    f.set("empty", {})
    assert f.get("empty") == {} and "empty" in f

    print("ttl_cache self-check: ok")


if __name__ == "__main__":
    _demo()
