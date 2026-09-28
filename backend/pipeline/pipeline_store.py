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

from backend import database

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


def _local_save_lead(lead: dict) -> dict:
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


def _local_set_stage(company_name: str, stage: str, note: str = "") -> dict:
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


def _local_remove_lead(company_name: str) -> bool:
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


def _local_list_leads(stage: str | None = None) -> list[dict]:
    with _connect() as con:
        if stage:
            rows = con.execute(
                "SELECT * FROM saved_leads WHERE stage = ? ORDER BY updated_at DESC",
                (stage,)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM saved_leads ORDER BY updated_at DESC").fetchall()
    return [_decode(r) for r in rows]


def _local_saved_names() -> set[str]:
    """Just the names, so the queue can mark rows already in the pipeline
    without shipping the whole saved list to the browser."""
    with _connect() as con:
        return {r[0] for r in con.execute("SELECT company_name FROM saved_leads")}


def _local_funnel() -> dict:
    """Counts per stage, every stage present even at zero so a dashboard does not
    silently drop the empty ones."""
    with _connect() as con:
        rows = con.execute(
            "SELECT stage, COUNT(*) n FROM saved_leads GROUP BY stage").fetchall()
    counts = {s: 0 for s in STAGES}
    for r in rows:
        counts[r["stage"]] = r["n"]
    return counts


def _local_events(company_name: str | None = None, limit: int = 200) -> list[dict]:
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


# -- Per-user store on Supabase (PREMORTEM.md sections 1 and 7) ---------------
# With Supabase configured every call acts as the signed-in BD: the backend
# forwards their JWT, PostgREST verifies it, and row level security scopes every
# read and write to auth.uid(). So one BD can neither see nor move another's
# leads, and every lead_events row is attributable - which is what the outcome
# data that will fit the winnability weights needs.
#
# There is deliberately NO SQLite fall-through here. A per-user list that
# silently dropped to one shared, ephemeral file would mix four people's
# pipelines and lose them on the next restart - worse than an honest error.
# Without Supabase configured at all (local dev) the SQLite path above is the
# whole store, exactly as before.


class AuthRequired(Exception):
    """No valid session: the caller must sign in (HTTP 401)."""


class StoreUnavailable(Exception):
    """Supabase is configured but did not answer (HTTP 503)."""


class Conflict(Exception):
    """The row changed between read and write (HTTP 409)."""


def _client(token: str | None):
    if not token:
        raise AuthRequired("sign in to use the pipeline")
    return database.user_client(token)


def _call(label: str, fn):
    """Run one PostgREST call, translating failures into the three outcomes a
    caller has to tell apart."""
    try:
        return fn()
    except (AuthRequired, Conflict, KeyError, ValueError):
        raise
    except Exception as e:
        text = f"{type(e).__name__}: {e}"
        # PGRST301/302/303: JWT missing, expired or invalid.
        if "JWT" in text or "PGRST30" in text or "401" in text:
            raise AuthRequired("session expired - sign in again") from e
        log.error("pipeline %s failed: %s", label, text)
        raise StoreUnavailable(f"pipeline store unreachable ({label})") from e


def _match(rows: list[dict], name: str, cin: str = ""):
    """Same identity rule as the SQLite path's _find: CIN first, folded name
    second. Done in Python because a BD's list is tens of rows, not thousands.
    ponytail: re-reads the user's whole list per save; fine at BD scale, move
    to a `norm` column + index if a list ever grows into the thousands."""
    cin = (cin or "").strip().upper()
    if cin:
        for r in rows:
            if (r.get("cin") or "").upper() == cin:
                return r, "cin"
    key = norm_name(name)
    for r in rows:
        if norm_name(r.get("company_name", "")) == key:
            return r, "name"
    return None, ""


def _remote_decode(r: dict) -> dict:
    d = {k: v for k, v in r.items() if k != "user_id"}
    d["flags"] = d.get("flags") or {}
    d["agencies"] = d.get("agencies") or []
    d.setdefault("cin", None)
    return d


def _remote_rows(c, stage: str | None = None) -> list[dict]:
    q = c.table("saved_leads").select("*").order("updated_at", desc=True)
    if stage:
        q = q.eq("stage", stage)
    return q.execute().data or []


def _remote_event(c, company: str, event: str, detail: str = "",
                  winnability=None, flags=None) -> None:
    # user_id defaults to auth.uid() server-side; never sent from here.
    c.table("lead_events").insert({
        "company_name": company, "event": event, "detail": detail,
        "winnability": winnability, "flags": flags or {}, "at": _now()}).execute()


def _remote_save(c, lead: dict) -> dict:
    name = (lead.get("company_name") or "").strip()
    if not name:
        raise ValueError("company_name is required")
    cin = (lead.get("cin") or "").strip().upper()
    existing, matched_on = _match(_remote_rows(c), name, cin)
    if existing:
        if cin and not existing.get("cin"):
            try:  # the cin column arrives with the schema update; optional
                c.table("saved_leads").update({"cin": cin}).eq(
                    "company_name", existing["company_name"]).execute()
                existing["cin"] = cin
            except Exception as e:
                log.warning("could not store CIN (schema not updated?): %s", e)
        return _remote_decode(existing) | {"already_saved": True,
                                           "matched_on": matched_on}
    now = _now()
    flags = lead.get("flags") or {}
    row = {"company_name": name, "stage": "Identified",
           "winnability": lead.get("winnability"), "flags": flags,
           "agencies": lead.get("agencies_seen") or [], "notes": "",
           "saved_at": now, "updated_at": now}
    try:
        c.table("saved_leads").insert(row | ({"cin": cin} if cin else {})).execute()
    except Exception as e:
        text = str(e)
        if "23505" in text or "duplicate key" in text:
            # Two tabs clicked Add at once: the other one won. Same answer as
            # the idempotent path, not an error.
            existing, matched_on = _match(_remote_rows(c), name, cin)
            if existing:
                return _remote_decode(existing) | {"already_saved": True,
                                                   "matched_on": matched_on}
            raise
        if cin and "cin" in text:
            c.table("saved_leads").insert(row).execute()
        else:
            raise
    _remote_event(c, name, "saved", "added from queue", lead.get("winnability"), flags)
    return {"company_name": name, "cin": cin or None, "stage": "Identified",
            "already_saved": False, "matched_on": ""}


def _remote_set_stage(c, company_name: str, stage: str, note: str) -> dict:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    row, _ = _match(_remote_rows(c), company_name)
    if row is None:
        raise KeyError(company_name)
    patch = {"stage": stage, "updated_at": _now()}
    if note:
        patch["notes"] = note
    # Compare-and-set on the old stage: two tabs moving the same lead must not
    # both "win" and leave the event log disagreeing with the row.
    res = (c.table("saved_leads").update(patch)
           .eq("company_name", row["company_name"]).eq("stage", row["stage"])
           .execute())
    if not res.data:
        raise Conflict(f"{row['company_name']} was moved meanwhile - reload")
    _remote_event(c, row["company_name"], "stage", f"{row['stage']} -> {stage}",
                  row.get("winnability"), row.get("flags") or {})
    return {"company_name": row["company_name"], "stage": stage}


def _remote_remove(c, company_name: str) -> bool:
    row, _ = _match(_remote_rows(c), company_name)
    if row is None:
        return False
    c.table("saved_leads").delete().eq("company_name", row["company_name"]).execute()
    _remote_event(c, row["company_name"], "removed")
    return True


def _remote_events(c, company_name: str | None, limit: int) -> list[dict]:
    q = c.table("lead_events").select("*").order("at", desc=True).limit(limit)
    if company_name:
        q = q.eq("company_name", company_name)
    return [{k: v for k, v in r.items() if k != "user_id"} | {"flags": r.get("flags") or {}}
            for r in q.execute().data or []]


def per_user() -> bool:
    """True when leads are per-user Supabase rows and a session is required."""
    return database.supabase_configured()


def _dispatch(label: str, token, remote_fn, local_fn):
    if not per_user():
        return local_fn()
    c = _client(token)
    try:
        return _call(label, lambda: remote_fn(c))
    finally:
        c.session.close()


def save_lead(lead: dict, token: str | None = None) -> dict:
    return _dispatch("save", token, lambda c: _remote_save(c, lead),
                     lambda: _local_save_lead(lead))


def set_stage(company_name: str, stage: str, note: str = "",
              token: str | None = None) -> dict:
    return _dispatch("set_stage", token,
                     lambda c: _remote_set_stage(c, company_name, stage, note),
                     lambda: _local_set_stage(company_name, stage, note))


def remove_lead(company_name: str, token: str | None = None) -> bool:
    return _dispatch("remove", token, lambda c: _remote_remove(c, company_name),
                     lambda: _local_remove_lead(company_name))


def list_leads(stage: str | None = None, token: str | None = None) -> list[dict]:
    return _dispatch("list", token,
                     lambda c: [_remote_decode(r) for r in _remote_rows(c, stage)],
                     lambda: _local_list_leads(stage))


def saved_names(token: str | None = None) -> set[str]:
    return _dispatch("names", token,
                     lambda c: {r["company_name"] for r in _remote_rows(c)},
                     _local_saved_names)


def funnel(token: str | None = None) -> dict:
    def remote(c):
        counts = {s: 0 for s in STAGES}
        for r in _remote_rows(c):
            counts[r["stage"]] = counts.get(r["stage"], 0) + 1
        return counts
    return _dispatch("funnel", token, remote, _local_funnel)


def events(company_name: str | None = None, limit: int = 200,
           token: str | None = None) -> list[dict]:
    return _dispatch("events", token,
                     lambda c: _remote_events(c, company_name, limit),
                     lambda: _local_events(company_name, limit))


# -- self-check (temp DB, no network) ----------------------------------------

class _FakePostgrest:
    """In-memory stand-in for one user's PostgREST client, for the self-check
    only. Supports exactly the calls the per-user path makes."""

    def __init__(self, tables: dict):
        self.tables = tables
        self.session = type("S", (), {"close": lambda self: None})()

    def table(self, name):
        return _FakeQuery(self.tables.setdefault(name, []))


class _FakeQuery:
    def __init__(self, rows):
        self.rows, self.op, self.payload, self.filters = rows, "select", None, []
        self._limit, self._order = None, None

    def select(self, *_a, **_k): self.op = "select"; return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def update(self, patch): self.op, self.payload = "update", patch; return self
    def delete(self): self.op = "delete"; return self
    def eq(self, col, val): self.filters.append((col, val)); return self
    def limit(self, n): self._limit = n; return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def _hit(self, r):
        return all(r.get(c) == v for c, v in self.filters)

    def execute(self):
        res = type("R", (), {})()
        if self.op == "insert":
            if any(r.get("company_name") == self.payload.get("company_name")
                   for r in self.rows if "stage" in r):
                raise RuntimeError("23505 duplicate key value")
            self.rows.append(dict(self.payload))
            res.data = [self.payload]
        elif self.op == "update":
            hit = [r for r in self.rows if self._hit(r)]
            for r in hit:
                r.update(self.payload)
            res.data = hit
        elif self.op == "delete":
            hit = [r for r in self.rows if self._hit(r)]
            self.rows[:] = [r for r in self.rows if not self._hit(r)]
            res.data = hit
        else:
            hit = [dict(r) for r in self.rows if self._hit(r)]
            if self._order:
                col, desc = self._order
                hit.sort(key=lambda r: r.get(col) or "", reverse=desc)
            res.data = hit[: self._limit] if self._limit else hit
        return res


def _demo_remote() -> None:
    """The per-user path, against the in-memory fake."""
    real_per_user, real_uc = globals()["per_user"], database.user_client
    tables: dict = {}
    globals()["per_user"] = lambda: True
    database.user_client = lambda token: _FakePostgrest(tables)
    try:
        try:
            list_leads(token=None)
            raise AssertionError("no token must mean sign in, not a shared list")
        except AuthRequired:
            pass
        t = "jwt"
        first = save_lead({"company_name": "Berar Finance Limited",
                           "winnability": 50, "flags": {"inc_tagged": True}}, token=t)
        assert first["already_saved"] is False
        dup = save_lead({"company_name": "Berar Finance Ltd"}, token=t)
        assert dup["already_saved"] and dup["matched_on"] == "name", dup
        assert set_stage("Berar Finance Ltd", "Contacted", "called CFO", token=t)["stage"] == "Contacted"
        assert funnel(token=t)["Contacted"] == 1
        assert list_leads(token=t)[0]["notes"] == "called CFO"
        assert saved_names(token=t) == {"Berar Finance Limited"}
        try:
            set_stage("Berar Finance Ltd", "Nearly", token=t)
            raise AssertionError("unknown stage should be refused")
        except ValueError:
            pass
        assert remove_lead("Berar Finance Limited", token=t) is True
        assert remove_lead("Berar Finance Limited", token=t) is False
        hist = [e["event"] for e in events("Berar Finance Limited", token=t)]
        assert sorted(hist) == ["removed", "saved", "stage"], hist

        # An unreachable Supabase is an error the UI can name, never a
        # silent fall back to the shared file.
        def broken(_token):
            raise RuntimeError("connection refused")
        database.user_client = lambda token: type("C", (), {
            "table": lambda self, n: broken(n),
            "session": type("S", (), {"close": lambda self: None})()})()
        try:
            list_leads(token=t)
            raise AssertionError("an outage must raise StoreUnavailable")
        except StoreUnavailable:
            pass
    finally:
        globals()["per_user"], database.user_client = real_per_user, real_uc

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

            _demo_remote()
            print("pipeline_store self-check: ok")
        finally:
            DB_PATH = real


if __name__ == "__main__":
    _demo()
