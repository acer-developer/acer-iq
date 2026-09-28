"""
Search persistence.

Two backends, chosen by what is configured, because the failure this prevents is
concrete: `_search_cache` in main.py is in-process, so a restart loses every
search and `/api/export/{id}` starts 404-ing at whoever was about to download it.

  Supabase  when SUPABASE_URL / SUPABASE_KEY are set.
  SQLite    otherwise - the default, because no Supabase project exists (every
            .env in the tree still carries the README's `your_url_here`).

SQLite is not a placeholder here. A single always-on box (ROADMAP_V3 phase 1) is
exactly what it is good at, and it needs no credentials, so search export works
out of the box instead of silently degrading.

Self-check (temp DB, no network):  python -m backend.database
"""
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from backend.config import settings

log = logging.getLogger("acer-iq.db")

_client = None
_init_failed = False

DB_PATH = Path(__file__).parent / "registry" / "data" / "pipeline.sqlite"

# Searches are a convenience for re-downloading a CSV, not a record to keep
# forever. Pruning on write keeps the file from growing unbounded on a
# long-running host without needing a scheduled job.
_KEEP_SEARCHES = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id        TEXT PRIMARY KEY,
    city      TEXT,
    industry  TEXT,
    results   TEXT NOT NULL,   -- JSON list of company dicts
    saved_at  TEXT NOT NULL
);
"""


def supabase_configured() -> bool:
    return bool(settings.supabase_url) and settings.supabase_url != "your_url_here"


def get_client():
    """The Supabase client, or None when it is not configured or is broken."""
    global _client, _init_failed
    if _client is not None:
        return _client
    if _init_failed:
        return None  # already logged; don't repeat it on every search
    if not supabase_configured():
        return None
    try:
        from supabase import create_client
        # The service-role key when set: it is what may write the archives
        # (supabase_schema.sql grants the publishable key read only).
        _client = create_client(settings.supabase_url,
                                settings.supabase_service_key or settings.supabase_key)
    except Exception as e:
        log.error("Supabase client init failed - falling back to SQLite: %s: %s",
                  type(e).__name__, e)
        _client, _init_failed = None, True
    return _client


def _sqlite():
    return sqlite_at(DB_PATH, _SCHEMA)


def backend_name() -> str:
    """Which store is actually in use - surfaced in /api/health so nobody has to
    guess whether their searches are being persisted."""
    return "supabase" if get_client() else "sqlite"


# -- Shared durable-store helpers ---------------------------------------------
# Every archive in this app follows one rule (PREMORTEM.md section 1): Supabase
# when it answers, SQLite otherwise. pipeline.sqlite is gitignored and Render
# declares no disk, so SQLite is the local-dev path and the outage safety net,
# never the archive of record. These helpers are that rule, written once - the
# archive modules (action_history, news_archive, source_health) call them rather
# than each growing their own copy of the fall-through.

def remote(label: str, fn):
    """Run `fn(client)` against Supabase.

    Returns (True, result) when Supabase answered, (False, None) when it is not
    configured, (False, exception) when the call failed. A failure is logged,
    never raised: the caller falls through to SQLite and a missing table
    (schema not yet run) must not take a page down with it."""
    client = get_client()
    if not client:
        return False, None
    try:
        return True, fn(client)
    except Exception as e:
        log.error("Supabase %s failed, falling back to SQLite (not durable in "
                  "production): %s: %s", label, type(e).__name__, e)
        return False, e


# PostgREST caps a response at 1000 rows by default; read in pages below that.
_PAGE = 1000


def select_all(client, table: str, cols: str, order: tuple[str, ...],
               where=None) -> list[dict]:
    """Every row of `table` (optionally narrowed by `where(query)`), paged.
    `order` must be unique (the primary key) or paging can skip or repeat rows.
    Raises - wrap it in remote()."""
    out: list[dict] = []
    start = 0
    while True:
        q = client.table(table).select(cols)
        if where is not None:
            q = where(q)
        for c in order:
            q = q.order(c)
        page = q.range(start, start + _PAGE - 1).execute().data or []
        out.extend(page)
        if len(page) < _PAGE:
            return out
        start += _PAGE


@contextmanager
def sqlite_at(path: Path, schema: str):
    """Commit-or-rollback AND close, with `schema` applied first. `with
    sqlite3.connect(...)` only commits, so without the explicit close every call
    leaks a file handle."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(schema)
        with con:
            yield con
    finally:
        con.close()


def missing_table(err) -> bool:
    """Is this failure "the table does not exist yet" (supabase_schema.sql not
    re-run) rather than Supabase being down? The two need different answers:
    a missing table is a known setup step, an outage must not be guessed past."""
    text = f"{type(err).__name__}: {err}" if err else ""
    return any(k in text for k in ("42P01", "PGRST205", "does not exist",
                                   "Could not find the table", "schema cache"))


def safe_url(url: str) -> str:
    """The URL if it is http(s), else "". Links in the archives come from
    third-party feeds; a `javascript:` href rendered as "Source" would run in
    a BD's session. Applied on write and again on read."""
    u = (url or "").strip()
    return u if u.lower().startswith(("https://", "http://")) else ""


_users: dict[str, tuple[float, dict | None]] = {}


def verify_user(token: str | None) -> dict | None:
    """{id, email} for a valid Supabase session token, else None. Asks Supabase
    Auth itself, so a forged or expired token is refused - never trust a
    token's contents just because it decodes. Cached 5 minutes per token."""
    import time
    import httpx
    if not supabase_configured() or not token:
        return None
    hit = _users.get(token)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        r = httpx.get(f"{settings.supabase_url.rstrip('/')}/auth/v1/user", timeout=10,
                      headers={"apikey": settings.supabase_key,
                               "Authorization": f"Bearer {token}"})
        body = r.json() if r.status_code == 200 else None
        user = ({"id": body["id"], "email": (body.get("email") or "").lower()}
                if body and body.get("id") else None)
    except Exception as e:
        log.warning("Supabase auth check failed: %s: %s", type(e).__name__, e)
        return None                    # not cached: a blip must not lock anyone out
    if len(_users) > 500:
        _users.clear()
    _users[token] = (time.time(), user)
    return user


_ids: dict[str, tuple[float, dict]] = {}


def user_ids_by_email() -> dict:
    """email -> auth user id, via the Auth admin API (service key only).
    Lets a lead be owned by the BD it is assigned to, not whoever clicked."""
    import time
    import httpx
    if not (supabase_configured() and settings.supabase_service_key):
        return {}
    hit = _ids.get("all")
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        r = httpx.get(f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users",
                      params={"page": 1, "per_page": 200}, timeout=15,
                      headers={"apikey": settings.supabase_service_key,
                               "Authorization": f"Bearer {settings.supabase_service_key}"})
        users = r.json().get("users", []) if r.status_code == 200 else []
    except Exception as e:
        log.warning("Supabase user list failed: %s: %s", type(e).__name__, e)
        return {}
    out = {(u.get("email") or "").lower(): u["id"] for u in users if u.get("id")}
    _ids["all"] = (time.time(), out)
    return out


def user_client(token: str):
    """A PostgREST client acting as the signed-in user, so row level security
    applies (auth.uid() is theirs). None when Supabase is not configured.

    Per request, not shared: the shared client's auth header is process-wide,
    and swapping it between concurrent requests would hand one BD another's
    pipeline. PostgREST verifies the JWT itself, so this backend needs no
    signing secret."""
    if not supabase_configured() or not token:
        return None
    import httpx
    from postgrest import SyncPostgrestClient
    base = f"{settings.supabase_url.rstrip('/')}/rest/v1"
    headers = {"apikey": settings.supabase_key,
               "Authorization": f"Bearer {token}",
               "Accept": "application/json",
               "Content-Type": "application/json"}
    return SyncPostgrestClient(
        base, headers=headers,
        http_client=httpx.Client(base_url=base, headers=headers, timeout=15))


def save_search(search_id: str, city: str, industry: str, companies: list) -> None:
    payload = json.dumps([
        c.model_dump() if hasattr(c, "model_dump") else c for c in companies
    ])

    client = get_client()
    if client:
        try:
            client.table("searches").upsert({
                "id": search_id, "city": city,
                "industry": industry, "results": payload,
            }).execute()
            return
        except Exception as e:
            # Fall through to SQLite rather than lose the search: a failed
            # upsert used to mean a 404 at export time with no second chance.
            log.error("Supabase save_search(%s) failed, falling back to SQLite:"
                      " %s: %s", search_id, type(e).__name__, e)

    try:
        with _sqlite() as con:
            con.execute(
                "INSERT OR REPLACE INTO searches (id, city, industry, results, saved_at)"
                " VALUES (?,?,?,?,?)",
                (search_id, city, industry, payload,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
            con.execute(
                "DELETE FROM searches WHERE id NOT IN ("
                "  SELECT id FROM searches ORDER BY saved_at DESC LIMIT ?)",
                (_KEEP_SEARCHES,))
    except Exception as e:
        log.error("save_search(%s) failed - CSV export will break after a "
                  "restart: %s: %s", search_id, type(e).__name__, e)


def load_search(search_id: str):
    client = get_client()
    if client:
        try:
            res = client.table("searches").select("*").eq("id", search_id).execute()
            if res.data:
                return res.data[0]
        except Exception as e:
            log.error("Supabase load_search(%s) failed, trying SQLite: %s: %s",
                      search_id, type(e).__name__, e)

    try:
        with _sqlite() as con:
            row = con.execute("SELECT * FROM searches WHERE id = ?",
                              (search_id,)).fetchone()
            return dict(row) if row else None
    except Exception as e:
        log.error("load_search(%s) failed: %s: %s", search_id, type(e).__name__, e)
    return None


def _demo() -> None:
    import tempfile
    global DB_PATH
    real = DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        DB_PATH = Path(tmp) / "t.sqlite"
        try:
            assert load_search("nope") is None

            save_search("abc", "Mumbai", "NBFC", [{"name": "Acme Ltd"}])
            row = load_search("abc")
            assert row and row["city"] == "Mumbai", row
            assert json.loads(row["results"])[0]["name"] == "Acme Ltd"

            # Re-saving the same id must overwrite, not raise on the primary key.
            save_search("abc", "Pune", "Bank", [{"name": "Beta Ltd"}])
            assert load_search("abc")["city"] == "Pune"

            # Pruning keeps the table bounded on a long-running host.
            global _KEEP_SEARCHES
            keep = _KEEP_SEARCHES
            _KEEP_SEARCHES = 3
            try:
                for i in range(5):
                    save_search(f"s{i}", "X", "Y", [{"name": f"C{i}"}])
                with _sqlite() as con:
                    n = con.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
                assert n <= 3, f"pruning failed, {n} rows left"
            finally:
                _KEEP_SEARCHES = keep

            print("database self-check: ok")
        finally:
            DB_PATH = real


if __name__ == "__main__":
    _demo()
