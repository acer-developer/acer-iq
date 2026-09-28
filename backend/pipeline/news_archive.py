"""
NEWS ARCHIVE - every news item we read, kept, never discarded (BUILD_PLAN Phase 1).

Before this, market_news.py and rss_news.py fetched live and threw the result
away on every refresh: nothing downstream could spot a trend in a feed that is
discarded, and "what did the papers say about X last month" had no answer.

Now every poll folds what it fetched in here. Append-only: an item is written
once, on first sight, and `first_seen` is the date ACER-IQ read it - the "read
date" every figure on screen has to carry (BUILD_PLAN invariant 2).

Dedup: on a key derived from the source and the item's link (or, for an item
with no link, its company + date + headline). The same RSS item is refetched on
every poll and must not multiply.

Raw text is stored and classification happens on read (news_classify.py), so
tightening the "major only" rules later re-applies to the whole history
instead of only to items read after the change.

Durable like every archive here: Supabase `news_archive` when it answers,
SQLite otherwise (backend/database.py), and `stats()["durable"]` says which.

Self-check (temp DB, no network):  python -m backend.pipeline.news_archive
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from backend import database

log = logging.getLogger("acer-iq.news_archive")

DB_PATH = Path(__file__).parent.parent / "registry" / "data" / "pipeline.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_archive (
    key          TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    company      TEXT NOT NULL DEFAULT '',
    symbol       TEXT NOT NULL DEFAULT '',
    date         TEXT NOT NULL DEFAULT '',   -- YYYY-MM-DD as published, '' if unknown
    subject      TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    categories   TEXT NOT NULL DEFAULT '[]', -- JSON list, the fetcher's own tags
    link         TEXT NOT NULL DEFAULT '',
    first_seen   TEXT NOT NULL                -- when ACER-IQ read it (UTC ISO)
);
CREATE INDEX IF NOT EXISTS idx_news_first_seen ON news_archive(first_seen);
"""

_TABLE = "news_archive"
_COLS = ("key", "source", "company", "symbol", "date", "subject",
         "description", "categories", "link", "first_seen")


def _connect():
    return database.sqlite_at(DB_PATH, _SCHEMA)


def key_for(item: dict) -> str:
    """Stable identity for one item across refetches."""
    src = item.get("source", "")
    link = (item.get("link") or item.get("attachment") or "").strip()
    basis = f"{src}|{link}" if link else "|".join(
        (src, item.get("company", ""), item.get("date", ""), item.get("subject", "")))
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:24]


def _row(item: dict, now: str) -> dict:
    return {
        "key": key_for(item),
        "source": item.get("source", "") or "",
        "company": (item.get("company") or "")[:300],
        "symbol": item.get("symbol", "") or "",
        "date": item.get("date", "") or "",
        "subject": (item.get("subject") or "")[:600],
        "description": (item.get("description") or "")[:600],
        "categories": json.dumps(item.get("categories") or []),
        "link": database.safe_url(item.get("link") or item.get("attachment") or "")[:1000],
        "first_seen": now,
    }


def record(items: list[dict]) -> int:
    """Fold a batch of freshly fetched items in. Returns how many were new.
    Never raises: a write failure must not take the news poll down with it."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows, seen = [], set()
    for it in items or []:
        if not it.get("source") or not (it.get("subject") or it.get("link")):
            continue
        r = _row(it, now)
        if r["key"] not in seen:   # one batch can repeat an item across feeds
            seen.add(r["key"])
            rows.append(r)
    if not rows:
        return 0

    ok, res = database.remote("news_archive.record", lambda c: c.table(_TABLE).upsert(
        rows, on_conflict="key", ignore_duplicates=True).execute())
    if ok:
        return len(res.data or [])
    try:
        with _connect() as con:
            before = con.execute("SELECT COUNT(*) FROM news_archive").fetchone()[0]
            con.executemany(
                "INSERT OR IGNORE INTO news_archive (" + ",".join(_COLS) + ") VALUES ("
                + ",".join(f":{c}" for c in _COLS) + ")", rows)
            after = con.execute("SELECT COUNT(*) FROM news_archive").fetchone()[0]
        return after - before
    except Exception as e:
        log.error("news_archive.record failed, the archive will have a gap: %s: %s",
                  type(e).__name__, e)
        return 0


def _decode(r: dict) -> dict:
    d = {c: r.get(c) for c in _COLS}
    d["link"] = database.safe_url(d.get("link") or "")
    try:
        d["categories"] = json.loads(d.get("categories") or "[]")
    except (TypeError, ValueError):
        d["categories"] = []
    return d


def since(days: int) -> list[dict]:
    """Every archived item published (or, if undated, first read) within `days`.

    The store is narrowed on `first_seen` - safe as a superset, because an item
    cannot be read before it is published - and the exact cut is made here on
    the published date. Both stores are read and unioned on the key, so items
    written to SQLite during a Supabase outage still show."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    ok, remote_rows = database.remote("news_archive read", lambda c: database.select_all(
        c, _TABLE, ",".join(_COLS), ("key",),
        where=lambda q: q.gte("first_seen", cutoff)))
    try:
        with _connect() as con:
            local = [dict(r) for r in con.execute(
                "SELECT * FROM news_archive WHERE first_seen >= ?", (cutoff,))]
    except Exception as e:
        log.error("news_archive SQLite read failed: %s: %s", type(e).__name__, e)
        local = []
    merged = {r["key"]: r for r in local}
    merged.update({r["key"]: r for r in (remote_rows if ok else []) or []})
    out = []
    for r in merged.values():
        d = _decode(r)
        effective = d["date"] or (d["first_seen"] or "")[:10]
        if effective >= cutoff:
            out.append(d)
    out.sort(key=lambda d: (d["date"] or d["first_seen"][:10], d["first_seen"]),
             reverse=True)
    return out


def stats() -> dict:
    """How deep the archive really is, and whether it survives a restart."""
    ok, res = database.remote("news_archive.stats", lambda c: (
        c.table(_TABLE).select("first_seen", count="exact")
        .order("first_seen").limit(1).execute()))
    if ok:
        return {"items": res.count or 0,
                "collecting_since": res.data[0]["first_seen"] if res.data else None,
                "store": "supabase", "durable": True}
    local = {"store": "sqlite", "durable": False}
    try:
        with _connect() as con:
            n = con.execute("SELECT COUNT(*) FROM news_archive").fetchone()[0]
            oldest = con.execute("SELECT MIN(first_seen) FROM news_archive").fetchone()[0]
    except Exception:
        return {"items": 0, "collecting_since": None} | local
    return {"items": n, "collecting_since": oldest} | local


def _demo() -> None:
    import tempfile
    global DB_PATH
    real, real_client = DB_PATH, database.get_client
    database.get_client = lambda: None   # never write to a configured Supabase
    with tempfile.TemporaryDirectory() as tmp:
        DB_PATH = Path(tmp) / "t.sqlite"
        try:
            today = date.today().isoformat()
            old = (date.today() - timedelta(days=90)).isoformat()
            batch = [
                {"source": "Economic Times", "subject": "RBI hikes repo rate",
                 "link": "https://et.example/a", "date": today, "categories": []},
                {"source": "NSE", "company": "Acme Ltd", "subject": "Allotment of NCDs",
                 "attachment": "https://nse.example/b.pdf", "date": old,
                 "categories": ["fund_raise"]},
                {"source": "LiveMint", "subject": "Undated item", "link": "https://lm.example/c"},
            ]
            assert record(batch) == 3
            assert record(batch) == 0, "refetch must not multiply rows"
            # The same item twice in one batch is one row.
            assert record([batch[0], dict(batch[0])]) == 0
            assert stats()["items"] == 3 and stats()["durable"] is False

            recent = since(7)
            assert {r["subject"] for r in recent} == {"RBI hikes repo rate", "Undated item"}, recent
            nse = [r for r in since(365) if r["source"] == "NSE"][0]
            assert nse["categories"] == ["fund_raise"], nse
            assert nse["link"] == "https://nse.example/b.pdf", nse
            assert nse["first_seen"][:10] == today, "read date is when we read it"

            # A non-http link is never stored or served.
            record([{"source": "X", "subject": "evil", "link": "javascript:alert(1)"}])
            assert [r["link"] for r in since(1) if r["subject"] == "evil"] == [""]

            # An item with no headline and no link carries nothing to keep.
            assert record([{"source": "X"}]) == 0
            # An item with no link still dedupes on its headline.
            nolink = {"source": "NSE", "company": "B Ltd", "subject": "Board outcome",
                      "date": today}
            assert record([nolink]) == 1 and record([nolink]) == 0
            print("news_archive self-check: ok")
        finally:
            DB_PATH, database.get_client = real, real_client


if __name__ == "__main__":
    _demo()
