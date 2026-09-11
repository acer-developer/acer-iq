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
        _client = create_client(settings.supabase_url, settings.supabase_key)
    except Exception as e:
        log.error("Supabase client init failed - falling back to SQLite: %s: %s",
                  type(e).__name__, e)
        _client, _init_failed = None, True
    return _client


@contextmanager
def _sqlite():
    """Commit-or-rollback AND close. `with sqlite3.connect(...)` only commits,
    so without the explicit close every call leaks a file handle."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_SCHEMA)
        with con:
            yield con
    finally:
        con.close()


def backend_name() -> str:
    """Which store is actually in use - surfaced in /api/health so nobody has to
    guess whether their searches are being persisted."""
    return "supabase" if get_client() else "sqlite"


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
