"""
MONTHLY BD LIST - what each of the four BDs calls this month, and why each name
(BUILD_PLAN Phase 4, Tab 2).

On first request in a month the list is generated from the signals ACER-IQ
already holds, ranked, split across the four BDs, and FROZEN: stored as a
snapshot and served from the snapshot for the rest of the month. A live feed
cannot be measured; a monthly list can, and "why was I given this name" stays
answerable months later because the reason, the sources and the inputs'
health are stored with it (PREMORTEM.md section 4).

Inputs (all existing, no new data source):
  queue      rating actions at other agencies, scored by winnability (lead_queue)
  macro      names hit by a macro event with thin coverage / maturing debt (macro)
  news       major company news - debt raises, rating actions, capex, deals
             (news_archive + news_classify)
  refinance  listed debt maturing inside 9 months (refinance, BSE)

Deterministic: same inputs -> same list. Score = winnability + a fixed bonus
per signal; ties broken by name; split by snake draft (1-2-3-4-4-3-2-1...)
so no BD always gets the weaker name of each round. A dead input does not
stop generation - the list is built from what answered and `stale_inputs`
names what did not, on screen and in the snapshot.

Every name carries: the instrument to pitch, the play, a one-sentence reason,
and every source with its read date. ACER clients and credit-screen failures
are never assigned.

Durable like every archive: Supabase `bd_lists` when it answers, SQLite
otherwise (backend/database.py).

Self-check (no network):  python -m backend.pipeline.bd_list
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

from backend import database
from backend.pipeline import acer_book, news_archive
from backend.pipeline import news_classify as nc
from backend.pipeline.pipeline_store import norm_name

log = logging.getLogger("acer-iq.bd_list")

DB_PATH = Path(__file__).parent.parent / "registry" / "data" / "pipeline.sqlite"
_ROSTER_PATH = Path(__file__).parent.parent / "data" / "bd_roster.json"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bd_lists (
    month        TEXT NOT NULL,          -- YYYY-MM
    version      INTEGER NOT NULL,
    generated_at TEXT NOT NULL,
    inputs       TEXT NOT NULL,          -- JSON: status of every input
    rows         TEXT NOT NULL,          -- JSON: the ranked, assigned list
    outcomes     TEXT,                   -- JSON: month-end result per name
    PRIMARY KEY (month, version)
);
"""
_TABLE = "bd_lists"
_COLS = ("month", "version", "generated_at", "inputs", "rows", "outcomes")

PER_BD = 10

# Fixed, readable bonuses on top of winnability (0-100). Flat on purpose:
# there is no outcome data to fit them to yet; month-end outcomes below are
# what will. ponytail: judgement weights - refit once lead_events has a few
# months of month_end rows.
_BONUS = {
    "debt_raise": 30,     # issuing now - the mandate is being placed now
    "refinance": 25,      # a dated deadline
    "rating_action": 15,  # just rated elsewhere - comparing agencies
    "thin_coverage": 10,
    "capex": 10,
    "deal": 10,
    "macro": 5,
}


def roster() -> list[dict]:
    """The four BDs, in draft order. Emails are optional: they only let a
    signed-in BD land on their own profile."""
    try:
        data = json.loads(_ROSTER_PATH.read_text(encoding="utf-8"))
        return [b for b in data if b.get("id") and b.get("name")]
    except Exception as e:
        log.error("bd_roster.json unreadable: %s", e)
        return []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- instrument to pitch ---------------------------------------------------------

_SHORT_TERM = re.compile(r"\bA[1-4]\+?(?!\w)|\bA[1-4]\b", re.I)
_STRUCTURED = re.compile(r"\((?:SO|CE)\s*\)|\bPTC\b|pass through|securiti[sz]", re.I)


def instrument_for(signals: list[dict], is_lender: bool) -> str:
    """The instrument to lead with, from the strongest evidence available:
    what the company is issuing, then what it was just rated on, then what
    kind of company it is."""
    text = " ".join(f"{s.get('detail', '')} {s.get('instrument', '')} {s.get('rating', '')}"
                    for s in signals)
    kinds = {s["kind"] for s in signals}
    if "debt_raise" in kinds:
        if re.search(r"commercial paper|\bCPs?\b", text, re.I):
            return "Commercial paper (CP) rating"
        if _STRUCTURED.search(text):
            return "Securitisation (PTC) rating"
        return "NCD / bond rating"
    if "refinance" in kinds:
        return "NCD rating for the refinancing issue"
    if _STRUCTURED.search(text):
        return "Securitisation / structured (SO) rating"
    inst = next((s.get("instrument") for s in signals if s.get("instrument")), "")
    if inst:
        return f"{inst} rating"
    if _SHORT_TERM.search(text):
        return "Short-term rating (CP / non-fund bank facilities)"
    if kinds & {"capex", "thin_coverage", "deal"} and not is_lender:
        return "Bank loan rating (term loan / working capital)"
    return "NCD / CP rating" if is_lender else "Bank loan rating (long-term facilities)"


def _play(kinds: set[str], instrument: str) -> str:
    if "debt_raise" in kinds:
        return f"Reach them before the issue is placed - offer a quick-TAT {instrument.lower()}."
    if "refinance" in kinds:
        return "Their listed debt matures soon: pitch the rating for the rollover issue now."
    if "rating_action" in kinds:
        return f"Just rated by another agency - offer ACER as a second opinion on the {instrument.lower()}."
    if kinds & {"thin_coverage", "macro"}:
        return "Macro pressure plus thin coverage - lead with lender-facing credibility of an independent rating."
    return f"Open with the {instrument.lower()} and ACER's turnaround time."


# -- assembly (pure) -------------------------------------------------------------

def assemble(signals_by_company: dict[str, dict], exclude: set[str],
             bds: list[dict], per_bd: int = PER_BD) -> list[dict]:
    """Rank and split. `signals_by_company`: norm name -> {name, winnability,
    blocked, is_lender, win_reason, signals: [{kind, text, source, url,
    read_at, ...}]}. Pure, so the self-check exercises it."""
    rows = []
    for key, c in signals_by_company.items():
        if key in exclude or c.get("blocked") or not c["signals"]:
            continue
        kinds = {s["kind"] for s in c["signals"]}
        score = (c.get("winnability") or 0) + sum(_BONUS.get(k, 0) for k in kinds)
        inst = instrument_for(c["signals"], c.get("is_lender", False))
        why = "; ".join(dict.fromkeys(s["text"] for s in c["signals"]))
        win = c.get("win_reason") or ("winnability unknown - not in the CRA archive yet"
                                      if c.get("winnability") is None else "")
        rows.append({
            "company_name": c["name"], "key": key, "score": score,
            "winnability": c.get("winnability"), "instrument": inst,
            "play": _play(kinds, inst),
            "reason": f"{c['name']}: {why}" + (f" ({win})" if win else "") + ".",
            "signals": sorted(kinds),
            "sources": [{"label": s["source"], "url": database.safe_url(s.get("url", "")),
                         "read_at": s.get("read_at", "")} for s in c["signals"]],
        })
    rows.sort(key=lambda r: (-r["score"], r["company_name"]))
    rows = rows[: per_bd * len(bds)] if bds else []

    # Snake draft: round 1 goes 1..n, round 2 n..1, so the BD picking last in
    # one round picks first in the next.
    n = len(bds)
    for i, r in enumerate(rows):
        rnd, pos = divmod(i, n)
        bd = bds[pos if rnd % 2 == 0 else n - 1 - pos]
        r.update(rank=i + 1, bd_id=bd["id"], bd_name=bd["name"])
    return rows


# -- inputs (network) ------------------------------------------------------------

def _is_lender(name: str) -> bool:
    return bool(set(nc.sectors_for(name)) & {"nbfc", "banking"})


async def _gather() -> tuple[dict, dict]:
    """Every input, degrading independently. Returns (by_company, inputs)."""
    from backend.pipeline import macro as macro_mod
    from backend.pipeline.lead_queue import build_queue

    by: dict[str, dict] = {}
    inputs: dict[str, dict] = {}

    def entry(name: str) -> dict:
        key = norm_name(name)
        e = by.get(key)
        if e is None:
            e = by[key] = {"name": name, "winnability": None, "blocked": False,
                           "is_lender": _is_lender(name), "win_reason": "", "signals": []}
        return e

    queue, macro, news_rows = await asyncio.gather(
        build_queue(days=30, enrich=15), macro_mod.build(14),
        asyncio.to_thread(news_archive.since, 30), return_exceptions=True)

    if isinstance(queue, Exception):
        inputs["queue"] = {"status": "unreachable", "detail": str(queue)[:200]}
    else:
        st = queue.get("coverage", {}).get("data_status", "unverified")
        inputs["queue"] = {"status": "ok" if st in ("ok", "none_found") else "stale",
                           "agencies_read": queue.get("coverage", {}).get("agencies_read", [])}
        for r in queue.get("leads", []):
            e = entry(r["company_name"])
            e["winnability"] = r.get("winnability")
            e["blocked"] = e["blocked"] or bool(r.get("blocked"))
            e["win_reason"] = (r.get("reasons") or [""])[0]
            e["signals"].append({
                "kind": "rating_action", "rating": r.get("latest_rating", ""),
                "text": f"{r.get('latest_action', 'rating action')} at "
                        f"{'/'.join(r.get('agencies_seen', [])) or 'another agency'} "
                        f"({r.get('latest_rating', '')}, {r.get('latest_date', '')})",
                "source": f"{'/'.join(r.get('agencies_seen', []))} press release",
                "url": r.get("source_url", ""), "read_at": r.get("latest_date", "")})

    if isinstance(macro, Exception):
        inputs["macro"] = {"status": "unreachable", "detail": str(macro)[:200]}
    else:
        stale = macro.get("coverage", {}).get("stale_inputs", [])
        inputs["macro"] = {"status": "stale" if stale else "ok", "stale_inputs": stale}
        if "BSE scrip master" in stale:
            inputs["refinance"] = {"status": "unreachable",
                                   "detail": "BSE scrip master could not be read"}
        else:
            inputs["refinance"] = {"status": "ok"}
        for block in macro.get("sectors", []):
            for c in block["companies"]:
                e = entry(c["company_name"])
                if c.get("winnability") is not None and e["winnability"] is None:
                    e["winnability"] = c["winnability"]
                e["blocked"] = e["blocked"] or bool(c.get("blocked"))
                for t in c["triggers"]:
                    kind = "refinance" if t["type"] == "refinance" else "thin_coverage"
                    e["signals"].append({"kind": kind, "text": t["text"],
                                         "source": t["source"], "url": t.get("url", ""),
                                         "read_at": t.get("read_at", "")})
                ev = block["events"][0] if block["events"] else None
                if ev:
                    e["signals"].append({
                        "kind": "macro", "text": f"in {block['sector_label']}, hit by \"{ev['subject'][:80]}\"",
                        "source": ev["source"], "url": ev.get("link", ""),
                        "read_at": ev.get("read_at", "")})

    if isinstance(news_rows, Exception):
        inputs["news"] = {"status": "unreachable", "detail": str(news_rows)[:200]}
    else:
        inputs["news"] = {"status": "ok", "items": len(news_rows)}
        for r in news_rows:
            if not r.get("company"):
                continue            # a headline with no named company cannot be assigned
            c = nc.classify(r)
            if not c["major"] or c["kind"] not in ("debt_raise", "rating_action", "capex", "deal"):
                continue
            e = entry(r["company"])
            e["signals"].append({
                "kind": c["kind"], "detail": f"{r.get('subject', '')} {r.get('description', '')}",
                "text": f"{r['source']} filing: {r.get('subject', '')[:90]}",
                "source": f"{r['source']} filing", "url": r.get("link", ""),
                "read_at": r.get("first_seen", "")})
    return by, inputs


# -- snapshot store --------------------------------------------------------------

def _connect():
    return database.sqlite_at(DB_PATH, _SCHEMA)


def _decode(r: dict) -> dict:
    out = dict(r)
    for k in ("inputs", "rows", "outcomes"):
        v = out.get(k)
        out[k] = json.loads(v) if isinstance(v, str) and v else (v or None)
    out["rows"] = out.get("rows") or []
    return out


def load(month: str) -> dict | None:
    """Latest stored snapshot for `month`, or None."""
    ok, rows = database.remote("bd_list load", lambda c: (
        c.table(_TABLE).select("*").eq("month", month)
        .order("version", desc=True).limit(1).execute().data))
    if ok:
        return _decode(rows[0]) if rows else None
    try:
        with _connect() as con:
            r = con.execute("SELECT * FROM bd_lists WHERE month = ? ORDER BY version DESC"
                            " LIMIT 1", (month,)).fetchone()
        return _decode(dict(r)) if r else None
    except Exception as e:
        log.error("bd_list SQLite load failed: %s", e)
        return None


def _store(snap: dict) -> str:
    row = {k: (json.dumps(snap[k]) if k in ("inputs", "rows", "outcomes") and snap.get(k) is not None
               else snap.get(k)) for k in _COLS}
    ok, _ = database.remote("bd_list store", lambda c: c.table(_TABLE).upsert(
        row, on_conflict="month,version").execute())
    if ok:
        return "supabase"
    with _connect() as con:
        con.execute("INSERT OR REPLACE INTO bd_lists (month, version, generated_at, inputs,"
                    " rows, outcomes) VALUES (:month,:version,:generated_at,:inputs,:rows,"
                    ":outcomes)", row)
    return "sqlite"


def month_key(d: date | None = None) -> str:
    return (d or date.today()).strftime("%Y-%m")


def _prev_month(month: str) -> str:
    y, m = map(int, month.split("-"))
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


_gen_lock = asyncio.Lock()


async def get_or_generate(month: str | None = None, force: bool = False) -> dict:
    """The month's frozen list, generating it on first request. `force`
    makes a new version (the old one is kept, never overwritten)."""
    month = month or month_key()
    async with _gen_lock:          # two tabs on the 1st must not build two lists
        existing = await asyncio.to_thread(load, month)
        if existing and not force:
            return existing | {"frozen": True}
        if month != month_key() and not existing:
            return {"month": month, "rows": [], "frozen": False,
                    "inputs": {}, "note": "No list was generated for that month."}

        # Close the previous month first, so its outcomes are recorded from
        # the pipeline as it stood when the month ended.
        prev = await asyncio.to_thread(load, _prev_month(month))
        if prev and not prev.get("outcomes"):
            await asyncio.to_thread(record_outcomes, prev)

        by, inputs = await _gather()
        bds = roster()
        rows = assemble(by, set(acer_book.client_names()), bds)
        snap = {"month": month, "version": (existing or {}).get("version", 0) + 1,
                "generated_at": _now_iso(), "inputs": inputs, "rows": rows, "outcomes": None}
        store = await asyncio.to_thread(_store, snap)
        return snap | {"frozen": True, "store": store, "durable": store == "supabase"}


def record_outcomes(snap: dict) -> dict:
    """Month-end: what became of each assigned name. Written into the snapshot
    for every name, and appended to lead_events for names somebody saved (the
    event needs the saver's user id). Needs the service key to read every
    BD's pipeline; without it, outcomes cannot be seen and are not faked."""
    ok, saved = database.remote("bd_list outcomes read", lambda c: database.select_all(
        c, "saved_leads", "company_name,user_id,stage", ("user_id", "company_name")))
    if not ok:
        log.warning("bd_list: month-end outcomes for %s not recorded - saved_leads unreadable",
                    snap.get("month"))
        return snap
    by_name = {norm_name(s["company_name"]): s for s in saved or []}
    outcomes, events = {}, []
    for r in snap.get("rows", []):
        s = by_name.get(r["key"])
        outcomes[r["company_name"]] = s["stage"] if s else "not worked"
        if s:
            events.append({"company_name": s["company_name"], "user_id": s["user_id"],
                           "event": "month_end",
                           "detail": f"BD list {snap['month']} ({r['bd_name']}): {s['stage']}"})
    if events:
        database.remote("bd_list outcomes events",
                        lambda c: c.table("lead_events").insert(events).execute())
    snap = snap | {"outcomes": outcomes}
    _store(snap)
    return snap


# -- self-check (no network) -----------------------------------------------------

def _demo() -> None:
    bds = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"},
           {"id": "c", "name": "C"}, {"id": "d", "name": "D"}]

    def co(name, win, kinds, lender=False, blocked=False, **extra):
        return {"name": name, "winnability": win, "blocked": blocked, "is_lender": lender,
                "win_reason": "", "signals": [dict({"kind": k, "text": f"{k} signal",
                                                    "source": "src", "url": "https://s",
                                                    "read_at": "2026-09-28"}, **extra) for k in kinds]}

    data = {norm_name(f"Co {i} Ltd"): co(f"Co {i} Ltd", i * 5, ["rating_action"]) for i in range(10)}
    data[norm_name("Issuer Ltd")] = co("Issuer Ltd", 40, ["debt_raise"], detail="Allotment of NCDs")
    data[norm_name("Bad Ltd")] = co("Bad Ltd", 90, ["rating_action"], blocked=True)
    data[norm_name("Client Ltd")] = co("Client Ltd", 90, ["debt_raise"])
    data[norm_name("Nothing Ltd")] = co("Nothing Ltd", 90, [])

    rows = assemble(data, {norm_name("Client Ltd")}, bds, per_bd=2)
    names = [r["company_name"] for r in rows]
    assert len(rows) == 8, rows
    assert "Bad Ltd" not in names and "Client Ltd" not in names and "Nothing Ltd" not in names
    # Debt raise (40 + 30) outranks a rating action at winnability 45 (45 + 15).
    assert names[0] == "Issuer Ltd" and rows[0]["instrument"] == "NCD / bond rating", rows[0]
    # Snake draft: A B C D D C B A.
    assert [r["bd_id"] for r in rows] == list("abcddcba"), [r["bd_id"] for r in rows]
    # Deterministic: same input, same list.
    assert assemble(data, {norm_name("Client Ltd")}, bds, per_bd=2) == rows
    # Every row has instrument, play, reason and a sourced read date.
    for r in rows:
        assert r["instrument"] and r["play"] and r["reason"]
        assert all(s["url"] and s["read_at"] for s in r["sources"]), r

    # Instrument rules.
    sig = lambda **k: [dict({"kind": "rating_action", "text": ""}, **k)]  # noqa: E731
    assert instrument_for(sig(rating="ACUITE A4+"), False).startswith("Short-term")
    assert instrument_for(sig(rating="ACUITE BBB+ (SO )"), True).startswith("Securitisation")
    assert instrument_for(sig(instrument="Cash Credit"), False) == "Cash Credit rating"
    assert instrument_for(sig(rating="IND A+"), True) == "NCD / CP rating"
    assert instrument_for([{"kind": "debt_raise", "detail": "issue of commercial paper"}], True) \
        .startswith("Commercial paper")
    assert instrument_for([{"kind": "refinance"}], False).startswith("NCD rating")

    # Snapshot round-trip on SQLite, versions kept.
    import tempfile
    global DB_PATH
    real, real_client = DB_PATH, database.get_client
    database.get_client = lambda: None
    with tempfile.TemporaryDirectory() as tmp:
        DB_PATH = Path(tmp) / "t.sqlite"
        try:
            snap = {"month": "2026-09", "version": 1, "generated_at": "x",
                    "inputs": {"queue": {"status": "ok"}}, "rows": rows, "outcomes": None}
            assert _store(snap) == "sqlite"
            got = load("2026-09")
            assert got["rows"] == rows and got["version"] == 1
            _store(snap | {"version": 2, "rows": rows[:1]})
            assert load("2026-09")["version"] == 2
            assert load("2026-08") is None
            # Without a readable pipeline, outcomes are not invented.
            assert record_outcomes(got).get("outcomes") is None
        finally:
            DB_PATH, database.get_client = real, real_client
    assert _prev_month("2026-01") == "2025-12" and _prev_month("2026-10") == "2026-09"
    assert [b["name"] for b in roster()] == ["Avinash", "Hema", "Akash", "Udit"], roster()
    print("bd_list self-check: ok")


if __name__ == "__main__":
    _demo()
