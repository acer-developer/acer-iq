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

Self-check (temp DB, no network):  python -m backend.pipeline.action_history
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


@contextmanager
def _connect():
    """Commit-or-rollback AND close. `with sqlite3.connect(...)` only commits, so
    without the explicit close every call leaks a file handle."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_SCHEMA)
        with con:
            yield con
    finally:
        con.close()


def record(actions: list[dict]) -> int:
    """Fold a batch of freshly fetched actions in. Returns how many were new.

    Never raises: this is a side effect of building the queue, and a write
    failure must not take the queue down with it. It is logged instead."""
    if not actions:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [(a.get("agency", ""), a.get("company_name", ""), a.get("rating", ""),
             a.get("action", ""), a.get("date", ""), a.get("isin", ""),
             a.get("source_url", ""), now)
            for a in actions if a.get("agency") and a.get("company_name")]
    try:
        with _connect() as con:
            before = con.execute("SELECT COUNT(*) FROM cra_actions").fetchone()[0]
            con.executemany(
                "INSERT OR IGNORE INTO cra_actions (agency, company_name, rating,"
                " action, date, isin, source_url, first_seen)"
                " VALUES (?,?,?,?,?,?,?,?)", rows)
            after = con.execute("SELECT COUNT(*) FROM cra_actions").fetchone()[0]
        return after - before
    except Exception as e:
        log.error("action_history.record failed, history will have a gap: %s: %s",
                  type(e).__name__, e)
        return 0


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
    try:
        with _connect() as con:
            rows = con.execute("SELECT * FROM cra_actions").fetchall()
    except Exception as e:
        log.error("action_history.since failed: %s: %s", type(e).__name__, e)
        return []

    out = []
    for r in rows:
        d = _parse(r["date"])
        # An undated action is kept, matching cra_press._within_days: dropping it
        # silently would be worse than showing it.
        if d is None or d >= cutoff:
            out.append({k: r[k] for k in r.keys() if k != "first_seen"})
    return out


def stats() -> dict:
    """How much history actually exists, so the UI can say so rather than imply
    a depth the archive does not have yet."""
    try:
        with _connect() as con:
            total = con.execute("SELECT COUNT(*) FROM cra_actions").fetchone()[0]
            oldest = con.execute(
                "SELECT MIN(first_seen) FROM cra_actions").fetchone()[0]
    except Exception:
        return {"actions": 0, "collecting_since": None}
    return {"actions": total, "collecting_since": oldest}


def merge(live: list[dict], archived: list[dict]) -> list[dict]:
    """Live rows win over archived ones on the same natural key, so a correction
    in a re-fetch is not shadowed by the copy we stored earlier."""
    def key(a: dict) -> tuple:
        return (a.get("agency", ""), a.get("company_name", ""),
                a.get("rating", ""), a.get("action", ""), a.get("date", ""))

    merged = {key(a): a for a in archived}
    merged.update({key(a): a for a in live})
    return list(merged.values())


def _demo() -> None:
    import tempfile
    global DB_PATH
    real = DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        DB_PATH = Path(tmp) / "t.sqlite"
        try:
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
            print("action_history self-check: ok")
        finally:
            DB_PATH = real


if __name__ == "__main__":
    _demo()
