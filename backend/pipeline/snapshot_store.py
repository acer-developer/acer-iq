"""
SNAPSHOT STORE - a durable daily record of what each CRA feed published.

THE PROBLEM THIS SOLVES. The three readable CRA feeds are latest-page
snapshots, not archives: Acuité serves page 1 of live ratings, Brickwork its
homepage rationale feed, India Ratings a rolling "latest ~10 actions". So
`days` in `fetch_recent_actions` filters locally and cannot ask for more
history - `days=365` returns exactly the same rows as `days=60`, and the queue
can only ever see roughly the last few weeks. An action published three weeks
ago and since pushed off page 1 is simply gone.

Appending every fetch to SQLite fixes that permanently, and costs nothing: the
queue already fetches these feeds on request, so the history accumulates as a
side effect of ordinary use. After a month of use the queue is drawing on a
month of actions instead of one page.

It is also the prerequisite for anything trend-shaped - "three withdrawals this
quarter", "second downgrade in sixty days" - which no single snapshot can ever
answer.

WHY NOT SUPABASE, which TODO.md names for this: there is no Supabase project,
and every .env in the tree still carries the README's placeholders. Same call
as pipeline_store.py - stdlib sqlite3 now beats blocked-on-credentials
forever. The row shape is deliberately portable if that changes.

Self-check (temp DB, no network):  python -m backend.pipeline.snapshot_store
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from backend.pipeline.cra_press import _within_days
from backend.pipeline.nse_ratings import _date_key

log = logging.getLogger("acer-iq.snapshot_store")

DB_PATH = Path(__file__).parent.parent / "registry" / "data" / "snapshots.sqlite"

# An action's identity. The feeds carry no stable id, so this is the natural
# key: the same instrument re-appearing on tomorrow's page 1 is the same
# action, not a new one. Getting this wrong in either direction is costly -
# too loose and a genuine second downgrade is swallowed as a duplicate, too
# tight and every refetch inflates the archive with phantom actions.
_IDENTITY = ("agency", "company_name", "rating", "action", "date")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agency       TEXT NOT NULL,
    company_name TEXT NOT NULL,
    rating       TEXT DEFAULT '',
    action       TEXT DEFAULT '',
    date         TEXT DEFAULT '',      -- the action's own date, as published
    isin         TEXT DEFAULT '',
    source_url   TEXT DEFAULT '',
    first_seen   TEXT NOT NULL,        -- when we first archived it
    last_seen    TEXT NOT NULL,        -- when we last saw it still published
    seen_count   INTEGER NOT NULL DEFAULT 1,
    UNIQUE (agency, company_name, rating, action, date)
);

CREATE INDEX IF NOT EXISTS idx_actions_date    ON feed_actions(date);
CREATE INDEX IF NOT EXISTS idx_actions_company ON feed_actions(company_name);
CREATE INDEX IF NOT EXISTS idx_actions_agency  ON feed_actions(agency);

-- One row per fetch. Without this an empty archive is ambiguous: nobody can
-- tell "we have never run" from "we ran and the agencies published nothing",
-- and those call for opposite responses.
CREATE TABLE IF NOT EXISTS snapshot_runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    sources   TEXT,              -- JSON: per-agency status from the fetch
    fetched   INTEGER NOT NULL,  -- actions the feeds returned
    added     INTEGER NOT NULL   -- of those, not already archived
    -- deliberately NOT unique on `at`: a run is an event, and `at` only has
    -- second precision, so two refreshes inside one second are two runs. A
    -- UNIQUE(at) here silently discarded the second one.
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connect():
    """Commit-or-rollback AND close - same reasoning as pipeline_store: a
    `with sqlite3.connect(...)` alone never closes, which leaks handles and on
    Windows holds the file locked."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_SCHEMA)
        with con:
            yield con
    finally:
        con.close()


def record_snapshot(actions: list[dict], sources: dict | None = None) -> dict:
    """Archive one fetch. Returns what was new, so a caller can log growth.

    Re-seeing an action already archived bumps last_seen and seen_count rather
    than inserting again - that is what keeps this idempotent when the queue is
    refreshed five times in an afternoon.
    """
    now = _now()
    added = 0
    with _connect() as con:
        for a in actions:
            key = tuple((a.get(k) or "") for k in _IDENTITY)
            if not key[1]:
                continue  # an action with no company is not addressable later
            cur = con.execute(
                "UPDATE feed_actions SET last_seen = ?, seen_count = seen_count + 1"
                " WHERE agency = ? AND company_name = ? AND rating = ?"
                "   AND action = ? AND date = ?",
                (now, *key))
            if cur.rowcount:
                continue
            con.execute(
                "INSERT INTO feed_actions (agency, company_name, rating, action,"
                " date, isin, source_url, first_seen, last_seen, seen_count)"
                " VALUES (?,?,?,?,?,?,?,?,?,1)",
                (*key, a.get("isin") or "", a.get("source_url") or "", now, now))
            added += 1

        con.execute(
            "INSERT INTO snapshot_runs (at, sources, fetched, added)"
            " VALUES (?,?,?,?)",
            (now, json.dumps(sources or {}), len(actions), added))

    if added:
        log.info("snapshot: archived %d new actions of %d fetched", added, len(actions))
    return {"fetched": len(actions), "added": added, "at": now}


def _row_to_action(r: sqlite3.Row) -> dict:
    """Back to the action dict the rest of the pipeline speaks, so archived and
    live rows are interchangeable to every caller downstream."""
    return {
        "agency": r["agency"],
        "company_name": r["company_name"],
        "rating": r["rating"],
        "action": r["action"],
        "date": r["date"],
        "isin": r["isin"],
        "source_url": r["source_url"],
    }


def actions_within(days: int, agency: str | None = None,
                   company_name: str | None = None) -> list[dict]:
    """Archived actions whose own published date falls inside the window.

    Filtered in Python on `_date_key`, not in SQL: the feeds publish dates as
    DD-MM-YYYY strings, which sort wrongly as text. Correctness beats the index
    here - the archive is thousands of rows, not millions.
    """
    sql = "SELECT * FROM feed_actions"
    where, args = [], []
    if agency:
        where.append("agency = ?")
        args.append(agency)
    if company_name:
        where.append("company_name = ?")
        args.append(company_name)
    if where:
        sql += " WHERE " + " AND ".join(where)

    with _connect() as con:
        rows = con.execute(sql, args).fetchall()

    # cra_press._within_days, deliberately: the live feed and the archive must
    # apply one identical window rule, including its "unknown date -> keep it
    # visible" policy. Two subtly different rules would put a row inside the
    # window when live and outside it once archived.
    out = [_row_to_action(r) for r in rows if _within_days(r["date"], days)]
    out.sort(key=lambda a: _date_key(a["date"]), reverse=True)
    return out


def merge_with_archive(live: list[dict], days: int) -> tuple[list[dict], dict]:
    """Live feed rows unioned with everything archived inside the window.

    This is the point of the module: the queue stops being capped at whatever
    sits on page 1 today. De-duplicated on the same identity used to archive,
    so an action present in both appears once.
    """
    seen = {tuple((a.get(k) or "") for k in _IDENTITY) for a in live}
    merged = list(live)
    recovered = 0
    for a in actions_within(days):
        key = tuple((a.get(k) or "") for k in _IDENTITY)
        if key in seen:
            continue
        seen.add(key)
        merged.append(a)
        recovered += 1
    merged.sort(key=lambda a: _date_key(a["date"]), reverse=True)
    return merged, {"live": len(live), "recovered": recovered, "total": len(merged)}


def stats() -> dict:
    """How much history exists, said plainly enough to print on the dashboard.

    `runs == 0` is reported distinctly from `actions == 0` on purpose - never
    run and ran-but-nothing-published are different facts."""
    with _connect() as con:
        total = con.execute("SELECT COUNT(*) FROM feed_actions").fetchone()[0]
        per_agency = {r["agency"]: r["n"] for r in con.execute(
            "SELECT agency, COUNT(*) n FROM feed_actions GROUP BY agency")}
        runs = con.execute("SELECT COUNT(*) FROM snapshot_runs").fetchone()[0]
        last = con.execute(
            "SELECT at, fetched, added FROM snapshot_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        first = con.execute(
            "SELECT MIN(first_seen) f FROM feed_actions").fetchone()["f"]

    depth_days = None
    if first:
        try:
            started = datetime.fromisoformat(first)
            depth_days = max(0, (datetime.now(timezone.utc) - started).days)
        except ValueError:
            pass

    return {
        "actions": total,
        "per_agency": per_agency,
        "runs": runs,
        "archiving_since": first,
        "depth_days": depth_days,
        "last_run": dict(last) if last else None,
    }


# -- self-check (temp DB, no network) ----------------------------------------

def _demo() -> None:
    import tempfile
    global DB_PATH
    real = DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        DB_PATH = Path(tmp) / "s.sqlite"
        try:
            today = datetime.now(timezone.utc).strftime("%d-%m-%Y")
            old = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%d-%m-%Y")

            a1 = {"agency": "ACUITE", "company_name": "Alpha Ltd", "rating": "ACUITE BBB",
                  "action": "Downgrade", "date": today, "isin": "", "source_url": "u1"}
            a2 = {"agency": "BRICKWORK", "company_name": "Beta Ltd", "rating": "BWR A-",
                  "action": "Withdrawn", "date": today, "isin": "", "source_url": "u2"}

            r = record_snapshot([a1, a2], {"ACUITE": "ok"})
            assert r == {"fetched": 2, "added": 2, "at": r["at"]}, r

            # Refetching the same page must not inflate the archive.
            r2 = record_snapshot([a1, a2])
            assert r2["added"] == 0, r2
            assert stats()["actions"] == 2

            # A genuinely new action is added.
            a3 = dict(a1, action="Upgrade")
            assert record_snapshot([a3])["added"] == 1
            assert stats()["actions"] == 3

            # An action with no company is not archivable - it could never be
            # looked up again.
            assert record_snapshot([{"agency": "X", "company_name": ""}])["added"] == 0

            # The window filters on the action's own published date.
            assert len(actions_within(30)) == 3
            record_snapshot([dict(a1, company_name="Gamma Ltd", date=old)])
            assert len(actions_within(30)) == 3, "a 120-day-old action is outside 30 days"
            assert len(actions_within(365)) == 4

            # The merge recovers what has fallen off page 1, and never doubles
            # an action that is still on it.
            merged, info = merge_with_archive([a1], days=30)
            assert info["live"] == 1 and info["total"] == 3, info
            assert info["recovered"] == 2, info
            names = sorted(m["company_name"] for m in merged)
            assert names == ["Alpha Ltd", "Alpha Ltd", "Beta Ltd"], names

            # Round-tripping preserves the action contract the pipeline speaks.
            got = [a for a in actions_within(30) if a["company_name"] == "Beta Ltd"][0]
            assert got == a2, got

            s = stats()
            assert s["runs"] == 5 and s["per_agency"]["ACUITE"] == 3, s
            assert s["last_run"]["added"] == 1, s
            assert s["depth_days"] == 0

            print("snapshot_store self-check: ok")
        finally:
            DB_PATH = real


if __name__ == "__main__":
    _demo()
