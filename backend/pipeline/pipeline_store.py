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

DE-DUPLICATION: one company must occupy one row however it was spelled by
whichever source found it. CIN is the only true identity here, so it wins when
we have one; otherwise the folded name (`norm_name`, shared with the queue's
grouping so the two can never disagree) stands in. The CRA feeds carry no CIN
at all, which is exactly why the name fallback is not optional.

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
    cin          TEXT,           -- identity when a source gave us one
    norm         TEXT,           -- folded name, the fallback identity
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


_SUFFIXES = (" PRIVATE LIMITED", " PVT LTD", " PVT. LTD.", " LIMITED", " LTD.", " LTD")


def norm_name(name: str) -> str:
    """Fold an issuer name so spelling variants of one company collapse together.

    Deliberately conservative: we would rather split one company into two rows
    than merge two companies into one and put the wrong rating against a name a
    salesperson is about to call. Lives here rather than in lead_queue so the
    queue's grouping and the pipeline's de-duplication cannot drift apart."""
    out = " ".join((name or "").upper().split())
    for suffix in _SUFFIXES:
        if out.endswith(suffix):
            out = out[: -len(suffix)]
            break
    return out.strip(" .,-")


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
        _migrate(con)
        with con:
            yield con
    finally:
        con.close()


def _migrate(con: sqlite3.Connection) -> None:
    """Add the identity columns to a database written before they existed, and
    backfill `norm` for rows already in it. Without the backfill every old row
    would be invisible to the de-duplication lookup and duplicate on next save."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(saved_leads)")}
    for col in ("cin", "norm"):
        if col not in cols:
            con.execute(f"ALTER TABLE saved_leads ADD COLUMN {col} TEXT")
    for name, in con.execute(
            "SELECT company_name FROM saved_leads WHERE norm IS NULL OR norm = ''"):
        con.execute("UPDATE saved_leads SET norm = ? WHERE company_name = ?",
                    (norm_name(name), name))


def _find(con: sqlite3.Connection, name: str, cin: str | None = None):
    """The saved row for this company, matched on CIN first and folded name
    second. Returns (row, matched_on) or (None, "")."""
    cin = (cin or "").strip().upper()
    if cin:
        row = con.execute("SELECT * FROM saved_leads WHERE cin = ?", (cin,)).fetchone()
        if row:
            return row, "cin"
    row = con.execute("SELECT * FROM saved_leads WHERE norm = ?",
                      (norm_name(name),)).fetchone()
    return (row, "name") if row else (None, "")


def _decode(row: sqlite3.Row) -> dict:
    """One saved_leads row as a dict, with the JSON columns decoded."""
    d = dict(row)
    d.pop("norm", None)   # internal identity key, not something a caller renders
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

    cin = (lead.get("cin") or "").strip().upper()

    with _connect() as con:
        existing, matched_on = _find(con, name, cin)
        if existing:
            # A CIN learned on a later search is worth keeping: it upgrades this
            # row from name-identity to real identity for every save after it.
            if cin and not existing["cin"]:
                con.execute("UPDATE saved_leads SET cin = ? WHERE company_name = ?",
                            (cin, existing["company_name"]))
                existing = con.execute(
                    "SELECT * FROM saved_leads WHERE company_name = ?",
                    (existing["company_name"],)).fetchone()
            # Parsed, not raw JSON strings: list_leads() already returns these
            # decoded, and one field with two shapes across endpoints is how a
            # caller ends up rendering a quoted blob at someone.
            # matched_on tells the caller *why* this was a duplicate, so a UI can
            # say "already saved as <other spelling>" instead of looking broken.
            return _decode(existing) | {"already_saved": True, "matched_on": matched_on}

        now = _now()
        flags = lead.get("flags") or {}
        con.execute(
            "INSERT INTO saved_leads (company_name, cin, norm, stage, winnability,"
            " flags, agencies, notes, saved_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, cin or None, norm_name(name), "Identified",
             lead.get("winnability"), json.dumps(flags),
             json.dumps(lead.get("agencies_seen") or []), "", now, now))
        _event(con, name, "saved", "added from queue",
               lead.get("winnability"), flags)
    return {"company_name": name, "cin": cin or None, "stage": "Identified",
            "already_saved": False, "matched_on": ""}


def set_stage(company_name: str, stage: str, note: str = "") -> dict:
    """Move a lead along the pipeline. Unknown stages are refused rather than
    written, or the funnel silently grows categories nobody can report on."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")

    with _connect() as con:
        row, _ = _find(con, company_name)
        if row is None:
            raise KeyError(company_name)
        company_name = row["company_name"]
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
        row, _ = _find(con, company_name)
        if row is None:
            return False
        con.execute("DELETE FROM saved_leads WHERE company_name = ?",
                    (row["company_name"],))
        _event(con, row["company_name"], "removed")
        return True


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

            # De-duplication: same company, two spellings, one row.
            save_lead({"company_name": "Nova Capital Private Limited",
                       "cin": "u65999mh2001plc123456"})
            dup = save_lead({"company_name": "Nova Capital Pvt Ltd"})
            assert dup["already_saved"] is True and dup["matched_on"] == "name", dup
            # CIN wins over a name that folds differently.
            other = save_lead({"company_name": "Nova Capital (India)",
                               "cin": "U65999MH2001PLC123456"})
            assert other["matched_on"] == "cin", other
            assert len(list_leads()) == 1, list_leads()
            # A CIN learned later upgrades a row saved without one.
            save_lead({"company_name": "Orbit Finserv Ltd"})
            save_lead({"company_name": "Orbit Finserv Limited", "cin": "U12345MH2010PLC000001"})
            orbit = [l for l in list_leads() if l["company_name"] == "Orbit Finserv Ltd"]
            assert len(orbit) == 1 and orbit[0]["cin"] == "U12345MH2010PLC000001", orbit
            # And a name variant still moves the row it matched.
            set_stage("Nova Capital Pvt Ltd", "Contacted")
            assert [l for l in list_leads()
                    if l["company_name"] == "Nova Capital Private Limited"][0]["stage"] == "Contacted"
            assert remove_lead("Nova Capital Pvt Ltd") is True
            assert remove_lead("Orbit Finserv Ltd") is True

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
