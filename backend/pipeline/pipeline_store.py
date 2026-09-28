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

# -- What a BD must record at each stage move (BD_LIST_SPEC.md section 3) ------
# Without next_followup_date there is no overdue list; without fee and size
# there is no revenue view - so these are refused, not just encouraged.
INSTRUMENTS = ["NCD", "CP", "BLR-LT", "BLR-ST", "Securitisation/PTC", "Other"]
CHANNELS = ["Email", "Call", "In-person", "Via banker", "LinkedIn"]
AGENCIES = ["CRISIL", "ICRA", "CARE", "India Ratings", "Acuite", "Infomerics", "Other"]
LOST_REASONS = ["Price", "Turnaround time", "Lost to another CRA", "Existing agency retained",
                "Issuer deferred/dropped issue", "Banker/lender preference",
                "Credit concern (ACER declined)", "No response after 3 attempts", "Not a fit"]

# field -> (kind, allowed values or None, required)
# ponytail: the detailed per-stage table below is kept for reference but
# replaced by SIMPLE_FIELDS (operator: statuses, not stages). Nothing reads
# it any more except validation of any detail a caller still sends.
DETAILED_STAGE_FIELDS: dict[str, dict[str, tuple]] = {
    "Identified": {},
    "Contacted": {"contact_name": ("text", None, True), "designation": ("text", None, True),
                  "channel": ("choice", CHANNELS, True), "contact_date": ("date", None, True),
                  "next_followup_date": ("date", None, True)},
    "Meeting": {"meeting_date": ("date", None, True), "attendees_client": ("text", None, True),
                "attendees_acer": ("text", None, True),
                "instrument_discussed": ("choice", INSTRUMENTS, True),
                "next_followup_date": ("date", None, True)},
    "Proposal": {"proposal_date": ("date", None, True), "instrument": ("choice", INSTRUMENTS, True),
                 "size_cr": ("number", None, True), "fee_quoted_rs": ("number", None, True),
                 "competing_agency": ("choice", AGENCIES + ["Unknown"], True),
                 "next_followup_date": ("date", None, True)},
    "Mandated": {"mandate_date": ("date", None, True), "instrument": ("choice", INSTRUMENTS, True),
                 "size_cr": ("number", None, True), "fee_agreed_rs": ("number", None, True),
                 "mandate_ref": ("text", None, True)},
    "Lost": {"lost_reason": ("choice", LOST_REASONS, True), "lost_to": ("choice", AGENCIES, False),
             "note": ("text", None, True)},
}


# Operator decision 2026-09-29: BDs use three statuses (Pending / In progress /
# Closed-won-or-lost - SIMPLE_STATUS below), with no mandatory fields. The
# field table is kept for anyone who does record detail, but nothing in it is
# required any more. Overrides BD_LIST_SPEC.md section 3.
MANDATORY_FIELDS = False

# The three statuses the UI shows, mapped onto the stored stages (the
# database constraint and every report already speak STAGES).
SIMPLE_STATUS = {"Pending": "Identified", "In progress": "Contacted",
                 "Won": "Mandated", "Lost": "Lost"}


SIMPLE_LOST_REASONS = ["Price", "TAT", "Went to other agency", "Issuer deferred", "No response"]
# Staleness replaces follow-up dates (Head of BD, 2026-09-29): no field to fill,
# nothing to game with a future date.
STALE_DAYS = {"Pending": 7, "In progress": 14}


def stale_days(status: str, last_updated: str, today: str) -> int | None:
    """Days past the staleness line, or None if not stale."""
    from datetime import date as _date
    limit = STALE_DAYS.get(status)
    if not limit or not last_updated:
        return None
    try:
        age = (_date.fromisoformat(today) - _date.fromisoformat(str(last_updated)[:10])).days
    except ValueError:
        return None
    return age - limit if age > limit else None


def simple_status(stage: str) -> str:
    """Stored stage -> what a BD sees."""
    if stage == "Identified":
        return "Pending"
    if stage == "Mandated":
        return "Won"
    if stage == "Lost":
        return "Lost"
    return "In progress"


_NOTE = ("text", None, False)
_BY = ("text", None, False)
STAGE_FIELDS: dict[str, dict[str, tuple]] = {
    st: {"note": _NOTE, "changed_by": _BY} for st in STAGES}
STAGE_FIELDS["Lost"] = {"lost_reason": ("choice", SIMPLE_LOST_REASONS, False),
                        "note": _NOTE, "changed_by": _BY}


def validate_stage(stage: str, details: dict | None) -> dict:
    """The cleaned details for a move to `stage`, or ValueError naming every
    missing or invalid field - so the form can say exactly what to fill."""
    from datetime import date as _date
    spec = STAGE_FIELDS.get(stage)
    if spec is None:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    details = details or {}
    clean, problems = {}, []
    for field, (kind, allowed, required) in spec.items():
        v = details.get(field)
        v = v.strip() if isinstance(v, str) else v
        if v in (None, ""):
            if required and MANDATORY_FIELDS:
                problems.append(f"{field} is required")
            continue
        if kind == "choice" and v not in allowed:
            problems.append(f"{field} must be one of {allowed}")
        elif kind == "date":
            try:
                v = _date.fromisoformat(str(v)[:10]).isoformat()
            except ValueError:
                problems.append(f"{field} must be a date (YYYY-MM-DD)")
        elif kind == "number":
            try:
                v = float(v)
                # "nan"/"inf" parse as floats and cannot go into jsonb.
                if not __import__("math").isfinite(v) or v <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                problems.append(f"{field} must be a positive number")
        elif kind == "text":
            v = str(v)[:300]
        clean[field] = v
    if MANDATORY_FIELDS and stage == "Lost" and clean.get("lost_reason") == "Lost to another CRA" \
            and not clean.get("lost_to"):
        problems.append("lost_to is required when lost_reason is 'Lost to another CRA'")
    if problems:
        raise ValueError(f"{stage} needs: " + "; ".join(problems))
    return clean


def _detail_text(old: str, new: str, clean: dict) -> str:
    extra = ", ".join(f"{k}={v}" for k, v in clean.items())
    return f"{old} -> {new}" + (f" | {extra}" if extra else "")

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
    for col in ("cin", "norm", "owner", "origin", "stage_details", "next_followup_date"):
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
    d["stage_details"] = json.loads(d.get("stage_details") or "{}")
    d["status"] = simple_status(d.get("stage", ""))
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
            "INSERT INTO saved_leads (company_name, cin, norm, owner, origin, stage, winnability,"
            " flags, agencies, notes, saved_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, cin or None, norm_name(name), (lead.get("owner") or "")[:40],
             lead.get("origin") or "list", "Identified",
             lead.get("winnability"), json.dumps(flags),
             json.dumps(lead.get("agencies_seen") or []), lead.get("reason") or "", now, now))
        _event(con, name, "saved", "added from queue",
               lead.get("winnability"), flags)
    return {"company_name": name, "cin": cin or None, "stage": "Identified",
            "already_saved": False, "matched_on": ""}


def _local_set_stage(company_name: str, stage: str, note: str = "",
                     details: dict | None = None) -> dict:
    """Move a lead along the pipeline. Unknown stages are refused rather than
    written, or the funnel silently grows categories nobody can report on;
    and each stage's mandatory fields are refused if missing (spec 3)."""
    clean = validate_stage(stage, details)

    with _connect() as con:
        row, _ = _find(con, company_name)
        if row is None:
            raise KeyError(company_name)
        company_name = row["company_name"]
        sd = json.loads(row["stage_details"] or "{}") if "stage_details" in row.keys() else {}
        sd[stage] = clean
        con.execute(
            "UPDATE saved_leads SET stage = ?, updated_at = ?, stage_details = ?,"
            " next_followup_date = ?,"
            " notes = CASE WHEN ? = '' THEN notes ELSE ? END"
            " WHERE company_name = ?",
            (stage, _now(), json.dumps(sd), clean.get("next_followup_date"),
             note, note, company_name))
        _event(con, company_name, "stage", _detail_text(row["stage"], stage, clean),
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


class Forbidden(Exception):
    """Signed in, but not allowed this action (HTTP 403)."""


def caller(token: str | None) -> dict:
    """Who is asking: {id, email, admin, profile}. Verified with Supabase Auth
    (database.verify_user) - never read off the token. Without Supabase
    (local dev) everyone is the single Admin.

    admin: email in ADMIN_EMAILS; if that is unset, every signed-in user.
    ponytail: the unset mode is only safe with Supabase sign-ups closed -
    /api/health reports it. profile: the roster BD whose email this is."""
    from backend.config import settings
    from backend.pipeline import roster
    if not per_user():
        return {"id": "", "email": "", "admin": True, "profile": ""}
    u = database.verify_user(token)
    if not u:
        raise AuthRequired("sign in to use the pipeline")
    admins = {e.strip().lower() for e in settings.admin_emails.split(",") if e.strip()}
    return u | {"admin": (u["email"] in admins) if admins else True,
                "profile": roster.profile_for_email(u["email"])}


def require_admin(token: str | None) -> dict:
    c = caller(token)
    if not c["admin"]:
        raise Forbidden("Admin only")
    return c


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


def _remote_decode(r: dict, me: str = "") -> dict:
    d = {k: v for k, v in r.items() if k != "user_id"}
    # Whether the signed-in BD owns this row: RLS lets them change only their
    # own, so the UI shows another BD's lead read-only instead of failing.
    d["mine"] = (r.get("user_id") == me) if me else True
    d.setdefault("owner", "")
    d["status"] = simple_status(d.get("stage", ""))
    d["flags"] = d.get("flags") or {}
    d["agencies"] = d.get("agencies") or []
    d.setdefault("cin", None)
    return d


def _remote_rows(c, stage: str | None = None) -> list[dict]:
    q = c.table("saved_leads").select("*").order("updated_at", desc=True)
    if stage:
        q = q.eq("stage", stage)
    return q.execute().data or []


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
    # Optional columns that arrive with a schema update; a database that has
    # not had it yet still takes the save without them.
    optional = {k: v for k, v in (("cin", cin), ("owner", (lead.get("owner") or "")[:40]),
                                  ("origin", lead.get("origin") or "")) if v}
    if lead.get("reason"):
        row["notes"] = lead["reason"][:500]
    try:
        c.table("saved_leads").insert(row | optional).execute()
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
        if optional and any(k in text for k in optional):
            c.table("saved_leads").insert(row).execute()
        else:
            raise
    # The lead_events row is written by the trg_log_lead_event trigger, in the
    # same transaction as this insert (supabase_schema.sql).
    return {"company_name": name, "cin": cin or None, "stage": "Identified",
            "already_saved": False, "matched_on": ""}


def _remote_set_stage(c, company_name: str, stage: str, note: str,
                      details: dict | None = None) -> dict:
    clean = validate_stage(stage, details)
    row, _ = _match(_remote_rows(c), company_name)
    if row is None:
        raise KeyError(company_name)
    sd = dict(row.get("stage_details") or {})
    sd[stage] = clean
    patch = {"stage": stage, "updated_at": _now(), "stage_details": sd,
             "next_followup_date": clean.get("next_followup_date")}
    if note:
        patch["notes"] = note
    # Compare-and-set on the old stage: two tabs moving the same lead must not
    # both "win" and leave the event log disagreeing with the row.
    res = (c.table("saved_leads").update(patch)
           .eq("company_name", row["company_name"]).eq("stage", row["stage"])
           .execute())
    if not res.data:
        raise Conflict(f"{row['company_name']} was moved meanwhile - reload")
    return {"company_name": row["company_name"], "stage": stage}


def _remote_remove(c, company_name: str) -> bool:
    row, _ = _match(_remote_rows(c), company_name)
    if row is None:
        return False
    c.table("saved_leads").delete().eq("company_name", row["company_name"]).execute()
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


class _ServiceAs:
    """The service client pinned to one user's rows, shaped like a user
    client, so _remote_save can write a lead straight into the assigned BD's
    pipeline when Admin adds it for them."""

    def __init__(self, sc, uid: str):
        self.sc, self.uid = sc, uid

    def table(self, name):
        t = self.sc.table(name)
        uid = self.uid

        class _T:
            def select(self_, *a, **k):
                return t.select(*a, **k).eq("user_id", uid)

            def insert(self_, row):
                return t.insert(row | {"user_id": uid})

            def update(self_, patch):
                return t.update(patch).eq("user_id", uid)
        return _T()


def save_lead(lead: dict, token: str | None = None) -> dict:
    owner = (lead.get("owner") or "").strip()
    if per_user() and owner:
        from backend.pipeline import roster
        who = caller(token)
        if owner != who["profile"] and not who["admin"]:
            raise Forbidden("only Admin can add a lead to another BD's pipeline")
        uid = database.user_ids_by_email().get(roster.email_for_profile(owner), "")
        if uid and uid != who["id"]:
            ok, res = database.remote("save for BD", lambda sc: _remote_save(_ServiceAs(sc, uid), lead))
            if not ok:
                raise StoreUnavailable("saving into another BD's pipeline needs SUPABASE_SERVICE_KEY")
            return res
    return _dispatch("save", token, lambda c: _remote_save(c, lead),
                     lambda: _local_save_lead(lead))


def set_stage(company_name: str, stage: str, note: str = "",
              token: str | None = None, details: dict | None = None) -> dict:
    return _dispatch("set_stage", token,
                     lambda c: _remote_set_stage(c, company_name, stage, note, details),
                     lambda: _local_set_stage(company_name, stage, note, details))


def remove_lead(company_name: str, token: str | None = None) -> bool:
    return _dispatch("remove", token, lambda c: _remote_remove(c, company_name),
                     lambda: _local_remove_lead(company_name))


def list_leads(stage: str | None = None, token: str | None = None) -> list[dict]:
    return _dispatch("list", token,
                     lambda c: [_remote_decode(r, _jwt_sub(token or "")) for r in _remote_rows(c, stage)],
                     lambda: _local_list_leads(stage))


def _jwt_sub(token: str) -> str:
    """The user id in a Supabase JWT, read WITHOUT verifying it - used only to
    mark which rows are the caller's. Every read and write is still checked by
    PostgREST against the verified token."""
    import base64
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part)).get("sub", "")
    except Exception:
        return ""


def list_all_leads(token: str | None = None, owner: str = "") -> list[dict]:
    """Every BD's saved leads - the Admin view - optionally one owner's.

    Needs a valid session (checked by a real query as that user) and the
    server-side service key, which bypasses RLS to read across BDs.
    ponytail: any signed-in BD can pick the Admin profile; the profile is a
    view, not a permission. Upgrade path: an `admins` table checked here."""
    def narrow(rows):
        return [r for r in rows if not owner or (r.get("owner") or "") == owner]
    if not per_user():
        return narrow(_local_list_leads())
    who = caller(token)
    # A BD may read their own profile's leads; everything else is Admin's.
    if not who["admin"] and not (owner and owner == who["profile"]):
        raise Forbidden("the team pipeline is Admin only")
    ok, rows = database.remote("pipeline all leads", lambda sc: database.select_all(
        sc, "saved_leads", "*", ("user_id", "company_name")))
    if not ok:
        raise StoreUnavailable("team pipeline needs SUPABASE_SERVICE_KEY on the server")
    me = who["id"]
    out = [_remote_decode(r, me) for r in rows or []]
    out.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return narrow(out)


_CIN_RE = __import__("re").compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")


def validate_self_sourced(lead: dict) -> None:
    """Spec 3: a BD's own lead still needs a real CIN and a one-sentence reason,
    so it is as answerable later as a list name."""
    cin = (lead.get("cin") or "").strip().upper()
    if not _CIN_RE.match(cin):
        raise ValueError("a self-sourced lead needs a valid 21-character CIN")
    if len((lead.get("reason") or "").strip()) < 15:
        raise ValueError("a self-sourced lead needs a one-sentence reason")


def reassign(company_name: str, new_owner: str, reason: str, token: str | None,
             valid_owners: set[str]) -> dict:
    """Admin only (spec 4): move a lead to another BD, with a reason, logged.
    The history moves with the name because it is the same row."""
    if new_owner not in valid_owners:
        raise ValueError(f"unknown BD {new_owner!r}")
    if len((reason or "").strip()) < 5:
        raise ValueError("a reassignment needs a reason")
    if not per_user():
        with _connect() as con:
            row, _ = _find(con, company_name)
            if row is None:
                raise KeyError(company_name)
            con.execute("UPDATE saved_leads SET owner = ?, updated_at = ? WHERE company_name = ?",
                        (new_owner, _now(), row["company_name"]))
            _event(con, row["company_name"], "reassigned",
                   f"{row['owner'] or 'unassigned'} -> {new_owner}: {reason.strip()[:300]}")
        return {"company_name": row["company_name"], "owner": new_owner}
    require_admin(token)
    from backend.pipeline import roster
    ok, rows = database.remote("pipeline reassign read", lambda sc: database.select_all(
        sc, "saved_leads", "company_name,user_id,owner", ("user_id", "company_name")))
    if not ok:
        raise StoreUnavailable("reassigning needs SUPABASE_SERVICE_KEY on the server")
    row, _ = _match(rows or [], company_name)
    if row is None:
        raise KeyError(company_name)
    # Control moves with the name: when the new BD has a mapped login, the row
    # becomes theirs (RLS works on user_id, not on the owner label).
    target_uid = database.user_ids_by_email().get(roster.email_for_profile(new_owner), "")
    patch = {"owner": new_owner, "updated_at": _now()}
    if target_uid and target_uid != row["user_id"]:
        if any(r["user_id"] == target_uid and norm_name(r["company_name"]) == norm_name(row["company_name"])
               for r in rows or []):
            raise Conflict(f"{new_owner} already has {row['company_name']} in their pipeline")
        patch["user_id"] = target_uid

    def write(sc):
        sc.table("saved_leads").update(patch).eq(
            "user_id", row["user_id"]).eq("company_name", row["company_name"]).execute()
        # The trigger logs the owner change; the reason is its own row.
        sc.table("lead_events").insert({
            "company_name": row["company_name"], "user_id": patch.get("user_id", row["user_id"]),
            "event": "note", "detail": f"reassigned to {new_owner}: {reason.strip()[:300]}"}).execute()
    ok, _ = database.remote("pipeline reassign", write)
    if not ok:
        raise StoreUnavailable("reassign failed")
    return {"company_name": row["company_name"], "owner": new_owner,
            "control_moved": "user_id" in patch,
            "note": "" if "user_id" in patch else
            f"{new_owner} has no mapped login yet - the lead is labelled theirs but stays "
            "editable by its previous owner until their email is in bd_roster.json"}


def team_summary(leads: list[dict], list_rows: list[dict], bds: list[dict],
                 today: str | None = None) -> dict:
    """The Admin screen (spec 4), per BD. Pure: leads = every BD's saved rows
    (with owner, stage, stage_details, next_followup_date, origin); list_rows =
    this month's BD-list rows."""
    from datetime import date as _date, datetime as _dt
    from zoneinfo import ZoneInfo
    today = today or _dt.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    rank = {s: i for i, s in enumerate(STAGES)}
    out = {}
    for b in bds:
        mine = [l for l in leads if (l.get("owner") or "") == b["id"]]
        assigned = [r for r in list_rows if r.get("bd_id") == b["id"] and not r.get("in_progress")]
        worked = {norm_name(l["company_name"]) for l in mine
                  if l.get("stage") != "Identified" and l.get("origin") != "self_sourced"}
        touched = sum(1 for r in assigned if r["key"] in worked)
        reached = lambda st: sum(1 for l in mine  # noqa: E731
                                 if rank.get(l.get("stage"), -1) >= rank[st] and l.get("stage") != "Lost"
                                 or st in (l.get("stage_details") or {}))
        sd = lambda l, st: (l.get("stage_details") or {}).get(st, {})  # noqa: E731
        overdue = []
        for l in mine:
            due = l.get("next_followup_date")
            if due and due < today and l.get("stage") not in ("Mandated", "Lost"):
                days = (_date.fromisoformat(today) - _date.fromisoformat(str(due)[:10])).days
                overdue.append({"company_name": l["company_name"], "stage": l["stage"],
                                "due": str(due)[:10], "days_late": days})
        lost = {}
        for l in mine:
            if l.get("stage") == "Lost":
                r = sd(l, "Lost").get("lost_reason", "not recorded")
                lost[r] = lost.get(r, 0) + 1
        contacted, proposal, mandated = reached("Contacted"), reached("Proposal"), reached("Mandated")
        out[b["id"]] = {
            "name": b["name"],
            "assigned": len(assigned), "touched": touched,
            "coverage_pct": round(100 * touched / len(assigned)) if assigned else None,
            "stages": {s: sum(1 for l in mine if l.get("stage") == s) for s in STAGES},
            "conv_contacted_to_proposal": round(100 * proposal / contacted) if contacted else None,
            "conv_proposal_to_mandated": round(100 * mandated / proposal) if proposal else None,
            "proposal_cr": sum(float(sd(l, "Proposal").get("size_cr", 0)) for l in mine if l.get("stage") == "Proposal"),
            "proposal_fees_rs": sum(float(sd(l, "Proposal").get("fee_quoted_rs", 0)) for l in mine if l.get("stage") == "Proposal"),
            "mandated_cr": sum(float(sd(l, "Mandated").get("size_cr", 0)) for l in mine if l.get("stage") == "Mandated"),
            "mandated_fees_rs": sum(float(sd(l, "Mandated").get("fee_agreed_rs", 0)) for l in mine if l.get("stage") == "Mandated"),
            "overdue": sorted(overdue, key=lambda o: -o["days_late"]),
            "lost_reasons": lost,
            "self_sourced": sum(1 for l in mine if l.get("origin") == "self_sourced"),
        }
        # The Head of BD's view under the status model (2026-09-29).
        st_counts = {"Pending": 0, "In progress": 0, "Won": 0, "Lost": 0}
        stale = []
        for l in mine:
            st = simple_status(l.get("stage", ""))
            st_counts[st] += 1
            late = stale_days(st, l.get("updated_at") or "", today)
            if late is not None:
                stale.append({"company_name": l["company_name"], "status": st, "days_over": late})
        out[b["id"]].update(
            status_counts=st_counts, won=st_counts["Won"],
            touched_pct=out[b["id"]]["coverage_pct"],
            stale=sorted(stale, key=lambda x: -x["days_over"]))
    # Clash: the same company (or CIN) in two BDs' pipelines.
    seen: dict[str, set] = {}
    for l in leads:
        for k in filter(None, (norm_name(l["company_name"]), (l.get("cin") or "").upper())):
            seen.setdefault(k, set()).add(l.get("owner") or "unassigned")
    clashes = sorted({k for k, owners in seen.items() if len(owners) > 1})
    unowned = sum(1 for l in leads if not l.get("owner"))
    return {"bds": out, "clashes": clashes, "unassigned_leads": unowned}


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

# Complete, valid stage details for the self-checks (spec 3 makes them mandatory).
EXAMPLE_DETAILS = {
    "Contacted": {"contact_name": "CFO", "designation": "CFO", "channel": "Call",
                  "contact_date": "2026-09-28", "next_followup_date": "2026-10-05"},
    "Proposal": {"proposal_date": "2026-09-28", "instrument": "NCD", "size_cr": 200,
                 "fee_quoted_rs": 450000, "competing_agency": "Unknown",
                 "next_followup_date": "2026-10-05"},
    "Lost": {"lost_reason": "Price", "note": "went with incumbent on price"},
    "Won": {"note": "mandate signed"},
}


class _FakePostgrest:
    """In-memory stand-in for one user's PostgREST client, for the self-check
    only. Supports exactly the calls the per-user path makes."""

    def __init__(self, tables: dict):
        self.tables = tables
        self.session = type("S", (), {"close": lambda self: None})()

    def table(self, name):
        return _FakeQuery(self.tables.setdefault(name, []), name, self.tables)


class _FakeQuery:
    def __init__(self, rows, name="", tables=None):
        self.rows, self.op, self.payload, self.filters = rows, "select", None, []
        self._limit, self._order = None, None
        self.name, self.tables = name, tables if tables is not None else {}

    def _trigger(self, event, row, detail=""):
        """What trg_log_lead_event does in Postgres."""
        if self.name == "saved_leads":
            self.tables.setdefault("lead_events", []).append({
                "company_name": row["company_name"], "event": event, "detail": detail,
                "flags": row.get("flags") or {}, "at": _now()})

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
            self._trigger("saved", self.payload, "added from queue")
            res.data = [self.payload]
        elif self.op == "update":
            hit = [r for r in self.rows if self._hit(r)]
            for r in hit:
                old = r.get("stage")
                r.update(self.payload)
                if r.get("stage") != old:
                    self._trigger("stage", r, f"{old} -> {r['stage']}")
            res.data = hit
        elif self.op == "delete":
            hit = [r for r in self.rows if self._hit(r)]
            self.rows[:] = [r for r in self.rows if not self._hit(r)]
            for r in hit:
                self._trigger("removed", r)
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
        assert set_stage("Berar Finance Ltd", "Contacted", "called CFO", token=t,
                         details=EXAMPLE_DETAILS["Contacted"])["stage"] == "Contacted"
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
            set_stage("Berar Finance Limited", "Proposal", details=EXAMPLE_DETAILS["Proposal"])
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

            # Mandatory fields are off (operator decision); a bare move works,
            # but a malformed value is still refused.
            set_stage("Berar Finance Limited", "Contacted")
            try:
                set_stage("Berar Finance Limited", "Lost", details={"lost_reason": "Bad luck"})
                raise AssertionError("an off-list lost reason should be refused")
            except ValueError:
                pass
            set_stage("Berar Finance Limited", "Proposal", details={"note": "sent terms"})
            assert list_leads()[0]["stage_details"]["Proposal"]["note"] == "sent terms"
            assert list_leads()[0]["status"] == "In progress"
            assert simple_status("Mandated") == "Won" and simple_status("Identified") == "Pending"
            assert stale_days("Pending", "2026-09-01", "2026-09-10") == 2
            assert stale_days("In progress", "2026-09-01", "2026-09-10") is None

            # Admin reassign (SQLite path) is logged with its reason.
            reassign("Berar Finance Limited", "hema", "Avinash on leave", None, {"hema", "udit"})
            assert list_leads()[0]["owner"] == "hema"
            assert any(e["event"] == "reassigned" and "on leave" in e["detail"]
                       for e in events("Berar Finance Limited"))
            try:
                reassign("Berar Finance Limited", "nobody", "reason here", None, {"hema"})
                raise AssertionError("unknown BD should be refused")
            except ValueError:
                pass

            # An unknown stage is refused, not written.
            try:
                set_stage("Berar Finance Limited", "Nearly")
                raise AssertionError("unknown stage should have been refused")
            except ValueError:
                pass

            try:
                set_stage("Nobody Ltd", "Contacted", details=EXAMPLE_DETAILS["Contacted"])
                raise AssertionError("unknown company should have raised")
            except KeyError:
                pass

            # History survives removal: a worked-and-dropped lead is outcome data.
            assert remove_lead("Berar Finance Limited") is True
            assert remove_lead("Berar Finance Limited") is False
            assert list_leads() == []
            hist = [e["event"] for e in events("Berar Finance Limited")]
            assert hist == ["removed", "reassigned", "stage", "stage", "stage", "saved"], hist

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
            set_stage("Nova Capital Pvt Ltd", "Contacted", details=EXAMPLE_DETAILS["Contacted"])
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
