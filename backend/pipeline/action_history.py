"""
ACTION HISTORY - accumulate the CRA feeds instead of only ever seeing today.

THE PROBLEM THIS FIXES: every CRA feed we can read is a "latest page" snapshot -
Acuite page 1, Brickwork's homepage list, Ind-Ra's latest ten. So the queue only
ever saw the last week or so, and `days=365` returned exactly the same rows as
`days=60`. The window parameter was decoration.

Now every queue build folds what it just fetched into SQLite. Nothing is
scheduled and nothing has to be backfilled: ordinary use accumulates the archive,
and the window becomes real from the day this ships forward. Two weeks of use
gives two weeks of history; a year gives a year.

Dedup is on the natural key (agency, company, rating, action, date) rather than
an id, because these feeds carry no stable identifier. Re-fetching the same page
every 15 minutes must not multiply rows.

WHERE IT LIVES: Supabase `cra_actions` when configured (supabase_schema.sql),
SQLite otherwise - the same fall-through as backend/database.py, reusing its
client. pipeline.sqlite is gitignored and Render declares no disk, so on its own
it is wiped on every restart (PREMORTEM.md section 1). SQLite is the local-dev
path and the safety net for a Supabase outage, not the archive of record.
Reads union both stores, so rows written to SQLite during an outage still show.
`stats()["durable"]` says which one is actually holding the history.

Self-check (temp DB, no network):  python -m backend.pipeline.action_history
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend import database

log = logging.getLogger("acer-iq.action_history")

DB_PATH = Path(__file__).parent.parent / "registry" / "data" / "pipeline.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cra_actions (
    agency       TEXT NOT NULL,
    company_name TEXT NOT NULL,
    rating       TEXT NOT NULL DEFAULT '',
    action       TEXT NOT NULL DEFAULT '',
    date         TEXT NOT NULL DEFAULT '',   -- DD-MM-YYYY, the app-wide format
    isin         TEXT NOT NULL DEFAULT '',
    source_url   TEXT NOT NULL DEFAULT '',
    first_seen   TEXT NOT NULL,
    PRIMARY KEY (agency, company_name, rating, action, date)
);

CREATE INDEX IF NOT EXISTS idx_actions_date ON cra_actions(date);
"""

_TABLE = "cra_actions"
_KEY = ("agency", "company_name", "rating", "action", "date")
_COLS = _KEY + ("isin", "source_url")


def _connect():
    return database.sqlite_at(DB_PATH, _SCHEMA)


def record(actions: list[dict]) -> int:
    """Fold a batch of freshly fetched actions in. Returns how many were new.

    Never raises: this is a side effect of building the queue, and a write
    failure must not take the queue down with it. It is logged instead."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [{c: a.get(c, "") or "" for c in _COLS} | {"first_seen": now}
            for a in (actions or []) if a.get("agency") and a.get("company_name")]
    for r in rows:
        r["source_url"] = database.safe_url(r["source_url"])
    if not rows:
        return 0

    # ON CONFLICT DO NOTHING: only genuinely new rows come back. Most likely
    # failure is supabase_schema.sql not yet run; SQLite then keeps the row for
    # this process's lifetime rather than losing it.
    ok, res = database.remote("action_history.record", lambda c: c.table(_TABLE).upsert(
        rows, on_conflict=",".join(_KEY), ignore_duplicates=True).execute())
    if ok:
        return len(res.data or [])

    try:
        with _connect() as con:
            before = con.execute("SELECT COUNT(*) FROM cra_actions").fetchone()[0]
            con.executemany(
                "INSERT OR IGNORE INTO cra_actions (agency, company_name, rating,"
                " action, date, isin, source_url, first_seen)"
                " VALUES (:agency,:company_name,:rating,:action,:date,:isin,"
                ":source_url,:first_seen)", rows)
            after = con.execute("SELECT COUNT(*) FROM cra_actions").fetchone()[0]
        return after - before
    except Exception as e:
        log.error("action_history.record failed, history will have a gap: %s: %s",
                  type(e).__name__, e)
        return 0


def _supabase_rows() -> list[dict]:
    """Every archived row from Supabase, or [] when it is not configured or
    not reachable (logged)."""
    ok, rows = database.remote("action_history read", lambda c: database.select_all(
        c, _TABLE, ",".join(_COLS), _KEY))
    return rows if ok else []


def _sqlite_rows() -> list[dict]:
    try:
        with _connect() as con:
            return [{c: r[c] for c in _COLS}
                    for r in con.execute("SELECT * FROM cra_actions").fetchall()]
    except Exception as e:
        log.error("action_history SQLite read failed: %s: %s", type(e).__name__, e)
        return []


def _parse(d: str):
    try:
        return datetime.strptime(d, "%d-%m-%Y")
    except (ValueError, TypeError):
        return None


def since(days: int) -> list[dict]:
    """Every archived action dated within `days` of today.

    Filtering happens in Python, not SQL: the dates are stored DD-MM-YYYY (the
    format the rest of the app speaks), which does not sort or compare correctly
    as text. Converting the column would silently reinterpret the rows already
    written by the scrapers, so the cost is paid here instead."""
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for r in merge(_supabase_rows(), _sqlite_rows()):
        d = _parse(r["date"])
        # An undated action is kept, matching cra_press._within_days: dropping it
        # silently would be worse than showing it.
        if d is None or d >= cutoff:
            out.append(r)
    return out


def stats() -> dict:
    """How much history actually exists, so the UI can say so rather than imply
    a depth the archive does not have yet - and where it lives, because a
    SQLite-only archive in production is gone on the next restart."""
    ok, res = database.remote("action_history.stats", lambda c: (
        c.table(_TABLE).select("first_seen", count="exact")
        .order("first_seen").limit(1).execute()))
    if ok:
        return {"actions": res.count or 0,
                "collecting_since": res.data[0]["first_seen"] if res.data else None,
                "store": "supabase", "durable": True}
    local = {"store": "sqlite", "durable": False}
    try:
        with _connect() as con:
            total = con.execute("SELECT COUNT(*) FROM cra_actions").fetchone()[0]
            oldest = con.execute(
                "SELECT MIN(first_seen) FROM cra_actions").fetchone()[0]
    except Exception:
        return {"actions": 0, "collecting_since": None} | local
    return {"actions": total, "collecting_since": oldest} | local


def merge(live: list[dict], archived: list[dict]) -> list[dict]:
    """Live rows win over archived ones on the same natural key, so a correction
    in a re-fetch is not shadowed by the copy we stored earlier."""
    def key(a: dict) -> tuple:
        return (a.get("agency", ""), a.get("company_name", ""),
                a.get("rating", ""), a.get("action", ""), a.get("date", ""))

    merged = {key(a): a for a in archived}
    merged.update({key(a): a for a in live})
    return list(merged.values())


class _FakeTable:
    """Stands in for a Supabase client in the self-check only. `rows` is what a
    read returns; `fail` makes every call raise, as a missing table does."""

    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.count = rows or [], fail, len(rows or [])
        self.data = None

    def table(self, _name):
        if self.fail:
            raise RuntimeError('relation "public.cra_actions" does not exist')
        return self

    def upsert(self, rows, **_kw):
        self.data = rows
        return self

    def select(self, *_a, **_kw):
        self.data = self.rows
        return self

    def order(self, *_a, **_kw):
        return self

    def range(self, *_a):
        return self

    def limit(self, _n):
        self.data = self.rows[:1]
        return self

    def execute(self):
        return self


def _demo() -> None:
    import tempfile
    global DB_PATH
    real, real_client = DB_PATH, database.get_client
    # Never let the self-check write to a real, configured Supabase.
    database.get_client = lambda: None
    with tempfile.TemporaryDirectory() as tmp:
        DB_PATH = Path(tmp) / "t.sqlite"
        try:
            assert stats()["durable"] is False
            today = datetime.now().strftime("%d-%m-%Y")
            old = (datetime.now() - timedelta(days=200)).strftime("%d-%m-%Y")

            batch = [
                {"agency": "ACUITE", "company_name": "Acme Ltd", "rating": "ACUITE A",
                 "action": "Reaffirmed", "date": today},
                {"agency": "INDRA", "company_name": "Beta Ltd", "rating": "IND BBB",
                 "action": "Upgrades", "date": old},
            ]
            assert record(batch) == 2

            # Re-fetching the same page must not multiply rows.
            assert record(batch) == 0, "dedup failed"
            assert stats()["actions"] == 2

            # The window is now real: the 200-day-old row is outside 30 days.
            recent = since(30)
            assert [a["company_name"] for a in recent] == ["Acme Ltd"], recent
            assert len(since(365)) == 2

            # An undated action is kept rather than silently dropped.
            record([{"agency": "CARE", "company_name": "Gamma Ltd",
                     "rating": "CARE AA", "action": "Current rating", "date": ""}])
            assert any(a["company_name"] == "Gamma Ltd" for a in since(1))

            # A row with no agency or no company is not storable.
            assert record([{"agency": "", "company_name": "", "date": today}]) == 0

            # merge(): the live copy wins on a shared key.
            live = [{"agency": "ACUITE", "company_name": "Acme Ltd",
                     "rating": "ACUITE A", "action": "Reaffirmed", "date": today,
                     "source_url": "fresh"}]
            merged = merge(live, since(365))
            acme = [a for a in merged if a["company_name"] == "Acme Ltd"]
            assert len(acme) == 1 and acme[0]["source_url"] == "fresh", acme
            assert len(merged) == 3, merged

            assert record([]) == 0

            # Supabase configured but the table missing (schema not yet run):
            # writes and reads fall through to SQLite, and stats says so.
            database.get_client = lambda: _FakeTable(fail=True)
            assert record([{"agency": "CARE", "company_name": "Delta Ltd",
                            "rating": "CARE A", "action": "Assigned",
                            "date": today}]) == 1
            assert any(a["company_name"] == "Delta Ltd" for a in since(30))
            st = stats()
            assert st["store"] == "sqlite" and st["durable"] is False, st

            # Supabase working: its rows are unioned with SQLite's, deduped on
            # the natural key, and stats reports the durable store.
            remote = [{"agency": "ACUITE", "company_name": "Acme Ltd",
                       "rating": "ACUITE A", "action": "Reaffirmed", "date": today,
                       "isin": "", "source_url": "", "first_seen": "2026-09-01"},
                      {"agency": "BWR", "company_name": "Eta Ltd",
                       "rating": "BWR BBB", "action": "Assigned", "date": today,
                       "isin": "", "source_url": "", "first_seen": "2026-09-02"}]
            database.get_client = lambda: _FakeTable(remote)
            names = [a["company_name"] for a in since(30)]
            assert names.count("Acme Ltd") == 1 and "Eta Ltd" in names, names
            assert "Delta Ltd" in names, "SQLite rows written in an outage vanished"
            st = stats()
            assert st == {"actions": 2, "collecting_since": "2026-09-01",
                          "store": "supabase", "durable": True}, st
            assert record(remote) == 2  # count = rows the upsert returned

            print("action_history self-check: ok")
        finally:
            DB_PATH, database.get_client = real, real_client


if __name__ == "__main__":
    _demo()
