"""
SOURCE HEALTH - the last successful read of every source, so an empty list can
say whether it is a quiet day or a dead scraper (PREMORTEM.md section 2).

THE FAILURE THIS PREVENTS: the queue legitimately reads "0 workable" some days,
and a dead scraper also reads "0 workable". Without a per-source record of
when each one last answered, a BD cannot tell the two apart, and after the
second empty morning they stop opening the tab. Sources rot here as routine
(three CRAs statically blocked, BSE retired endpoints in Sep 2026), so this is
not hypothetical.

Every fetcher calls `record(source, ok, count)` on every real network read.
`status()` turns that into one of five states:

  ok          the last read succeeded
  failing     the last read failed, but for less than 48 hours
  down        no successful read for 48 hours or more - the alarm threshold
  stale       nothing has even tried to read it for 48 hours
  never_read  no read since this store was created

Durable like every other archive here: Supabase `source_reads` when it
answers, SQLite otherwise (backend/database.py). "Unreachable since X" has to
survive a restart, or every Render restart would reset a two-day outage to a
fresh-looking "not read yet".

Self-check (temp DB, no network):  python -m backend.pipeline.source_health
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend import database

log = logging.getLogger("acer-iq.source_health")

DB_PATH = Path(__file__).parent.parent / "registry" / "data" / "pipeline.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_reads (
    source        TEXT PRIMARY KEY,
    last_attempt  TEXT,
    last_success  TEXT,
    failing_since TEXT,
    last_error    TEXT DEFAULT '',
    last_count    INTEGER
);
"""

_TABLE = "source_reads"
_COLS = ("source", "last_attempt", "last_success", "failing_since",
         "last_error", "last_count")

# PREMORTEM section 2: a source that has not answered in 48 hours raises an
# alarm. Shorter would page on an ordinary weekend with no filings.
ALARM_AFTER = timedelta(hours=48)

# Persist on every change of state, and otherwise at most this often per
# source - the RSS feeds alone are read five at a time on every news poll.
_PERSIST_EVERY = 600

_state: dict[str, dict] = {}
_persisted_at: dict[str, float] = {}
_loaded = False
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _connect():
    return database.sqlite_at(DB_PATH, _SCHEMA)


def _load() -> None:
    """Seed the in-process view from the durable store, once per process.
    ponytail: a second worker's writes are not seen until restart; Render runs
    one worker, so this is exact today. Re-read per call if that changes."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    ok, rows = database.remote("source_health load", lambda c: database.select_all(
        c, _TABLE, ",".join(_COLS), ("source",)))
    if not ok:
        try:
            with _connect() as con:
                rows = [dict(r) for r in con.execute("SELECT * FROM source_reads")]
        except Exception as e:
            log.error("source_health SQLite load failed: %s: %s", type(e).__name__, e)
            rows = []
    for r in rows or []:
        _state.setdefault(r["source"], {c: r.get(c) for c in _COLS})


def _persist(st: dict) -> None:
    row = {c: st.get(c) for c in _COLS}
    ok, _ = database.remote("source_health.record", lambda c: c.table(_TABLE).upsert(
        row, on_conflict="source").execute())
    if ok:
        return
    try:
        with _connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO source_reads (source, last_attempt,"
                " last_success, failing_since, last_error, last_count)"
                " VALUES (:source,:last_attempt,:last_success,:failing_since,"
                ":last_error,:last_count)", row)
    except Exception as e:
        log.error("source_health persist failed: %s: %s", type(e).__name__, e)


def _persist_soon(st: dict) -> None:
    """Persist without blocking: record() is called from inside async fetchers,
    and a synchronous Supabase write there would stall the event loop."""
    try:
        import asyncio
        asyncio.get_running_loop().run_in_executor(None, _persist, st)
    except RuntimeError:          # no running loop (CLI, self-check)
        _persist(st)


def record(source: str, ok: bool, count: int | None = None, error: str = "") -> None:
    """One real read of `source`. Never raises - it rides along every fetch,
    and a bookkeeping failure must not fail the fetch it describes."""
    try:
        with _lock:
            _load()
            now = _iso(_now())
            st = _state.get(source) or {c: None for c in _COLS} | {"source": source}
            was_ok = st.get("failing_since") is None and st.get("last_success") is not None
            st["last_attempt"] = now
            if ok:
                st.update(last_success=now, failing_since=None, last_error="",
                          last_count=count)
            else:
                st["failing_since"] = st.get("failing_since") or now
                st["last_error"] = (error or "no answer")[:300]
            _state[source] = st
            changed = was_ok != ok
            due = time.time() - _persisted_at.get(source, 0) > _PERSIST_EVERY
            if changed or due:
                _persisted_at[source] = time.time()
                persist = dict(st)
            else:
                persist = None
        if persist:
            _persist_soon(persist)
    except Exception as e:
        log.error("source_health.record(%s) failed: %s: %s", source, type(e).__name__, e)


def _ago(d: datetime, now: datetime) -> str:
    secs = int((now - d).total_seconds())
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{secs // 60} min ago"
    if secs < 172800:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} days ago"


def _stamp(d: datetime) -> str:
    return d.strftime("%d %b %H:%M UTC")


def status(source: str, now: datetime | None = None) -> dict:
    """One source's state plus a sentence a BD can read."""
    now = now or _now()
    with _lock:
        _load()
        st = dict(_state.get(source) or {})
    attempt, success = _parse(st.get("last_attempt")), _parse(st.get("last_success"))
    failing = _parse(st.get("failing_since"))

    if attempt is None:
        state, msg = "never_read", "not read yet"
    elif failing is not None:
        state = "down" if now - failing >= ALARM_AFTER else "failing"
        msg = f"unreachable since {_stamp(failing)}"
        if success:
            msg += f" (last good read {_stamp(success)})"
    elif now - attempt >= ALARM_AFTER:
        state, msg = "stale", f"not read since {_stamp(attempt)}"
    else:
        state, msg = "ok", f"read OK {_ago(success or attempt, now)}"
    return {"source": source, "state": state, "message": msg,
            "last_success": st.get("last_success"),
            "last_attempt": st.get("last_attempt"),
            "failing_since": st.get("failing_since"),
            "last_error": st.get("last_error") or "",
            "last_count": st.get("last_count"),
            "alarm": state in ("down", "stale")}


def snapshot(prefix: str = "") -> list[dict]:
    """Every known source (optionally only those starting with `prefix`)."""
    with _lock:
        _load()
        names = sorted(n for n in _state if n.startswith(prefix))
    return [status(n) for n in names]


def summarise(rows: list[dict]) -> dict:
    """What an empty list should say, given the sources that feed it.

    `verdict` is "quiet" only when every source answered: then an empty list
    really is no signal. Any failing/down source makes it "degraded"; no read
    at all makes it "unknown". The UI renders the three differently."""
    if not rows:
        return {"verdict": "unknown", "text": "No source has been read yet."}
    bad = [r for r in rows if r["state"] in ("failing", "down", "stale")]
    never = [r for r in rows if r["state"] == "never_read"]
    if bad:
        return {"verdict": "degraded",
                "text": "; ".join(f"{r['source']} {r['message']}" for r in bad)}
    if len(never) == len(rows):
        return {"verdict": "unknown", "text": "No source has been read yet."}
    return {"verdict": "quiet",
            "text": "All sources answered - " + "; ".join(
                f"{r['source']} {r['message']}" for r in rows if r["state"] == "ok")}


def _demo() -> None:
    import tempfile
    global DB_PATH, _loaded
    real, real_client = DB_PATH, database.get_client
    database.get_client = lambda: None   # never touch a configured Supabase
    with tempfile.TemporaryDirectory() as tmp:
        DB_PATH = Path(tmp) / "t.sqlite"
        _state.clear(); _persisted_at.clear(); _loaded = False
        try:
            assert status("NSE")["state"] == "never_read"
            assert summarise([])["verdict"] == "unknown"

            record("NSE", True, 12)
            s = status("NSE")
            assert s["state"] == "ok" and s["last_count"] == 12, s
            assert summarise([s])["verdict"] == "quiet"

            record("NSE", False, error="HTTP 403")
            s = status("NSE")
            assert s["state"] == "failing" and "unreachable since" in s["message"], s
            assert "last good read" in s["message"], s
            assert summarise([s])["verdict"] == "degraded"
            # The streak keeps its start: a second failure does not reset it.
            first = s["failing_since"]
            record("NSE", False, error="HTTP 403")
            assert status("NSE")["failing_since"] == first

            # 48 hours later the same failure is an alarm.
            later = _now() + ALARM_AFTER + timedelta(minutes=1)
            s = status("NSE", now=later)
            assert s["state"] == "down" and s["alarm"], s

            # Recovery clears the streak.
            record("NSE", True, 3)
            assert status("NSE")["failing_since"] is None

            # A source nobody polled for 48h is stale, and alarms too.
            assert status("NSE", now=later)["state"] == "stale"

            # "Unreachable since X" survives a restart (the durable store).
            record("BSE", False, error="timeout")
            _state.clear(); _loaded = False
            assert status("BSE")["state"] == "failing", status("BSE")
            assert [r["source"] for r in snapshot()] == ["BSE", "NSE"]
            print("source_health self-check: ok")
        finally:
            DB_PATH, database.get_client = real, real_client
            _state.clear(); _persisted_at.clear(); _loaded = False


if __name__ == "__main__":
    _demo()
