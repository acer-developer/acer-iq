"""
Shared plumbing for every external call the pipeline makes:

- TTLCache        — in-process cache so the same company / city / prompt is
                    never re-fetched within the TTL (fixes P4: no caching).
- limiter(source) — per-source asyncio.Semaphore so one search can't fire
                    90 concurrent BSE hits (fixes P4: ban risk).
- SourceStatus    — per-request accumulator of which sources succeeded,
                    failed or were skipped; surfaced in API responses so
                    failures are visible, never silent (fixes P2).
"""

import asyncio
import logging
import time

log = logging.getLogger("acer_iq.net")

# Negative results (empty/None) are cached briefly so a flaky source is
# retried soon, while real data sticks for the full TTL.
NEGATIVE_TTL = 300


class TTLCache:
    def __init__(self, maxsize: int = 4096):
        self._data: dict[str, tuple[float, object]] = {}
        self._maxsize = maxsize

    def get(self, key: str):
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value, ttl: float) -> None:
        if len(self._data) >= self._maxsize:
            # evict the oldest tenth — cheap and good enough for this size
            for k in sorted(self._data, key=lambda k: self._data[k][0])[
                : max(1, self._maxsize // 10)
            ]:
                self._data.pop(k, None)
        self._data[key] = (time.monotonic() + ttl, value)

    def set_result(self, key: str, value, ttl: float) -> None:
        """Cache a fetch result: full TTL for real data, short TTL for empties."""
        self.set(key, value, ttl if value else NEGATIVE_TTL)


cache = TTLCache()

# Concurrency caps per external source (one process-wide semaphore each)
_LIMITS = {
    "bse": 4,
    "zauba": 2,
    "llm": 2,
    "geocode": 1,
    "hunter": 2,
    "places": 3,
    "overpass": 2,
}
_semaphores: dict[str, asyncio.Semaphore] = {}


def limiter(source: str) -> asyncio.Semaphore:
    sem = _semaphores.get(source)
    if sem is None:
        sem = _semaphores[source] = asyncio.Semaphore(_LIMITS.get(source, 4))
    return sem


class SourceStatus:
    """Per-request tally of external-source outcomes.

    as_dict() →  {"bse": {"status": "degraded", "ok": 12, "failed": 3,
                          "detail": "timeout"}, ...}
    status: ok | degraded (some calls failed) | failed | skipped
    """

    def __init__(self):
        self._counts: dict[str, dict] = {}

    def _entry(self, source: str) -> dict:
        return self._counts.setdefault(
            source, {"ok": 0, "failed": 0, "skipped": 0, "detail": ""}
        )

    def ok(self, source: str) -> None:
        self._entry(source)["ok"] += 1

    def fail(self, source: str, detail: str = "") -> None:
        e = self._entry(source)
        e["failed"] += 1
        if detail:
            e["detail"] = detail

    def skip(self, source: str, detail: str = "") -> None:
        e = self._entry(source)
        e["skipped"] += 1
        if detail:
            e["detail"] = detail

    def as_dict(self) -> dict:
        out = {}
        for source, e in self._counts.items():
            if e["ok"] and e["failed"]:
                status = "degraded"
            elif e["ok"]:
                status = "ok"
            elif e["failed"]:
                status = "failed"
            else:
                status = "skipped"
            out[source] = {
                "status": status,
                "ok": e["ok"],
                "failed": e["failed"],
                "skipped": e["skipped"],
                "detail": e["detail"],
            }
        return out


def setup_logging() -> None:
    """Process-wide structured-ish logging: timestamp | level | logger | message."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    # uvicorn already configures its own handlers; don't double-log
    logging.getLogger("httpx").setLevel(logging.WARNING)
