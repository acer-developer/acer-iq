"""
PIPELINE STORE - saved leads and their outcome history, on SQLite.

ROADMAP_V3 phase 3, minus accounts. Supabase was scaffolded into this repo but
never set up, so rather than block on credentials this uses stdlib sqlite3, the
same way backend/registry/store.py already does. One shared list for the whole
BD team; per-user separation arrives with auth, not before.

WHY THE EVENT LOG MATTERS MORE THAN THE SAVED LIST: the winnability weights are
flat and un-tuned because there is no outcome data to fit them to. Nothing can
change that until stage transitions are actually recorded, so `lead_events` is
the point of this module. Every save and every stage change appends a row
carrying the flags that produced the lead, which is what makes it possible to
ask later which signal actually converts.

Self-check (temp DB, no network):  python -m backend.pipeline.pipeline_store
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("acer-iq.pipeline_store")

DB_PATH = Path(__file__).parent.parent / "registry" / "data" / "pipeline.sqlite"

STAGES = ["Identified", "Contacted", "Meeting", "Proposal", "Mandated", "Lost"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_leads (
    company_name TEXT PRIMARY KEY,
    stage        TEXT NOT NULL DEFAULT 'Identified',
    winnability  INTEGER,
    flags        TEXT,          -- JSON, the signals as they stood when saved
    agencies     TEXT,          -- JSON list
    notes        TEXT DEFAULT '',
    saved_at     TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lead_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    event        TEXT NOT NULL,   -- 'saved' | 'stage' | 'note' | 'removed'
    detail       TEXT DEFAULT '',
    winnability  INTEGER,
    flags        TEXT,
    at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_company ON lead_events(company_name);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connect():
    """Commit-or-rollback AND close.

    `with sqlite3.connect(...)` only commits; it never closes. Every call would
    leak a file handle, which a long-running server eventually runs out of - and
    on Windows it also keeps the file locked, which is how this was caught."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_SCHEMA)
        with con:
            yield con
    finally:
        con.close()


def _decode(row: sqlite3.Row) -> dict:
    """One saved_leads row as a dict, with the JSON columns decoded."""
    d = dict(row)
    d["flags"] = json.loads(d.get("flags") or "{}")
    d["agencies"] = json.loads(d.get("agencies") or "[]")
    return d


def _event(con: sqlite3.Connection, company: str, event: str,
           detail: str = "", winnability=None, flags=None) -> None:
    con.execute(
        "INSERT INTO lead_events (company_name, event, detail, winnability, flags, at)"
        " VALUES (?,?,?,?,?,?)",
        (company, event, detail, winnability, json.dumps(flags or {}), _now()))


def save_lead(lead: dict) -> dict:
    """Save a lead at stage Identified, or return the existing one untouched.

    Idempotent on purpose: two people clicking Add on the same company must not
    reset a lead that is already at Proposal back to Identified."""
    name = (lead.get("company_name") or "").strip()
    if not name:
        raise ValueError("company_name is required")

    with _connect() as con:
        existing = con.execute(
            "SELECT * FROM saved_leads WHERE company_name = ?", (name,)).fetchone()
        if existing:
            # Parsed, not raw JSON strings: list_leads() already returns these
            # decoded, and one field with two shapes across endpoints is how a
            # caller ends up rendering a quoted blob at someone.
            return _decode(existing) | {"already_saved": True}

        now = _now()
        flags = lead.get("flags") or {}
        con.execute(
            "INSERT INTO saved_leads (company_name, stage, winnability, flags,"
            " agencies, notes, saved_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (name, "Identified", lead.get("winnability"), json.dumps(flags),
             json.dumps(lead.get("agencies_seen") or []), "", now, now))
        _event(con, name, "saved", "added from queue",
               lead.get("winnability"), flags)
    return {"company_name": name, "stage": "Identified", "already_saved": False}


def set_stage(company_name: str, stage: str, note: str = "") -> dict:
    """Move a lead along the pipeline. Unknown stages are refused rather than
    written, or the funnel silently grows categories nobody can report on."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")

    with _connect() as con:
        row = con.execute("SELECT * FROM saved_leads WHERE company_name = ?",
                          (company_name,)).fetchone()
        if row is None:
            raise KeyError(company_name)
        con.execute(
            "UPDATE saved_leads SET stage = ?, updated_at = ?,"
            " notes = CASE WHEN ? = '' THEN notes ELSE ? END"
            " WHERE company_name = ?",
            (stage, _now(), note, note, company_name))
        _event(con, company_name, "stage", f"{row['stage']} -> {stage}",
               row["winnability"], json.loads(row["flags"] or "{}"))
    return {"company_name": company_name, "stage": stage}


def remove_lead(company_name: str) -> bool:
    """Drop a lead from the working list. The event history is deliberately kept:
    that a lead was worked and dropped is exactly the outcome data the scoring
    weights need."""
    with _connect() as con:
        cur = con.execute("DELETE FROM saved_leads WHERE company_name = ?",
                          (company_name,))
        if cur.rowcount:
            _event(con, company_name, "removed")
        return bool(cur.rowcount)


def list_leads(stage: str | None = None) -> list[dict]:
    with _connect() as con:
        if stage:
            rows = con.execute(
                "SELECT * FROM saved_leads WHERE stage = ? ORDER BY updated_at DESC",
                (stage,)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM saved_leads ORDER BY updated_at DESC").fetchall()
    return [_decode(r) for r in rows]


def saved_names() -> set[str]:
    """Just the names, so the queue can mark rows already in the pipeline
    without shipping the whole saved list to the browser."""
    with _connect() as con:
        return {r[0] for r in con.execute("SELECT company_name FROM saved_leads")}


def funnel() -> dict:
    """Counts per stage, every stage present even at zero so a dashboard does not
    silently drop the empty ones."""
    with _connect() as con:
        rows = con.execute(
            "SELECT stage, COUNT(*) n FROM saved_leads GROUP BY stage").fetchall()
    counts = {s: 0 for s in STAGES}
    for r in rows:
        counts[r["stage"]] = r["n"]
    return counts


def events(company_name: str | None = None, limit: int = 200) -> list[dict]:
    with _connect() as con:
        if company_name:
            rows = con.execute(
                "SELECT * FROM lead_events WHERE company_name = ?"
                " ORDER BY id DESC LIMIT ?", (company_name, limit)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM lead_events ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
    return [dict(r) | {"flags": json.loads(r["flags"] or "{}")} for r in rows]


# -- self-check (temp DB, no network) ----------------------------------------

def _demo() -> None:
    import tempfile
    global DB_PATH
    real = DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        DB_PATH = Path(tmp) / "t.sqlite"
        try:
            lead = {"company_name": "Berar Finance Limited", "winnability": 50,
                    "flags": {"inc_tagged": True, "multi_cra": True},
                    "agencies_seen": ["CARE", "INDRA"]}

            first = save_lead(lead)
            assert first["already_saved"] is False and first["stage"] == "Identified"

            # Saving twice must not reset progress.
            set_stage("Berar Finance Limited", "Proposal")
            again = save_lead(lead)
            assert again["already_saved"] is True and again["stage"] == "Proposal", again
            # Same shape as list_leads(), not raw JSON strings.
            assert again["flags"] == {"inc_tagged": True, "multi_cra": True}, again
            assert again["agencies"] == ["CARE", "INDRA"], again

            assert funnel()["Proposal"] == 1 and funnel()["Identified"] == 0
            assert saved_names() == {"Berar Finance Limited"}

            got = list_leads()[0]
            assert got["flags"]["inc_tagged"] is True
            assert got["agencies"] == ["CARE", "INDRA"]

            # An unknown stage is refused, not written.
            try:
                set_stage("Berar Finance Limited", "Nearly")
                raise AssertionError("unknown stage should have been refused")
            except ValueError:
                pass

            try:
                set_stage("Nobody Ltd", "Contacted")
                raise AssertionError("unknown company should have raised")
            except KeyError:
                pass

            # History survives removal: a worked-and-dropped lead is outcome data.
            assert remove_lead("Berar Finance Limited") is True
            assert remove_lead("Berar Finance Limited") is False
            assert list_leads() == []
            hist = [e["event"] for e in events("Berar Finance Limited")]
            assert hist == ["removed", "stage", "saved"], hist

            try:
                save_lead({"company_name": "  "})
                raise AssertionError("a blank name should have been refused")
            except ValueError:
                pass

            print("pipeline_store self-check: ok")
        finally:
            DB_PATH = real


if __name__ == "__main__":
    _demo()
