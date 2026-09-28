"""
MONTHLY BD LIST - what each of the four BDs calls this month, and why each name
(BUILD_PLAN Phase 4, Tab 2). Built to BD_LIST_SPEC.md - the Head of BD's spec;
read it before changing anything here.

On first request in a month the list is generated from the signals ACER-IQ
already holds, split across the BDs, and FROZEN: stored as a snapshot and
served from it for the rest of the month. A live feed cannot be measured; a
monthly list can, and "why was I given this name" stays answerable months
later because the reason, the sources and the inputs' health are stored with
it (PREMORTEM.md section 4).

Inputs (all existing, no new data source):
  queue      rating actions at other agencies, scored by winnability (lead_queue)
  macro      names hit by a macro event with thin coverage / maturing debt (macro)
  news       major company news - debt raises, rating actions, capex, deals
  refinance  listed debt maturing inside 9 months (via macro, BSE)
  pipeline   ACER's own history with the name (saved_leads / lead_events)

Split (spec 1): each BD takes up to 10 names from their own segment, by
score; unclassified names and last month's untouched names form a pool that
is snake-drafted to fill any BD still short. Below the score floor nothing is
listed - never padded. Last month's names still being worked stay with their
owner as "in progress" and do not use a slot (spec 5).

Deterministic: same inputs -> same list; ties by name.

Self-check (no network):  python -m backend.pipeline.bd_list
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from backend import database
from backend.pipeline import acer_book, news_archive
from backend.pipeline import news_classify as nc
from backend.pipeline.pipeline_store import STAGES, norm_name

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
MIN_SCORE = 20          # below this a name is not worth a BD's call - never pad

# Fixed, readable bonuses on top of winnability (0-100). ponytail: judgement
# weights - the spec defers tuning until 3 months of outcomes exist.
_BONUS = {"debt_raise": 30, "refinance": 25, "rating_action": 15,
          "thin_coverage": 10, "capex": 10, "deal": 10, "macro": 5}
TRIGGERS = tuple(_BONUS)                    # spec 2 allowed values, strongest first

INSTRUMENTS = ("NCD", "CP", "BLR-LT", "BLR-ST", "Securitisation/PTC", "Other")
NOT_VISIBLE = "not visible (CRISIL/ICRA/Infomerics not in feed)"
IN_PROGRESS = {"Contacted", "Meeting", "Proposal"}


def roster() -> list[dict]:
    """The BDs, in draft order, with the sectors their segment owns."""
    try:
        data = json.loads(_ROSTER_PATH.read_text(encoding="utf-8"))
        return [b for b in data if b.get("id") and b.get("name")]
    except Exception as e:
        log.error("bd_roster.json unreadable: %s", e)
        return []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- field derivations (pure) -------------------------------------------------------

_SHORT_TERM = re.compile(r"\bA[1-4]\+?(?!\w)", re.I)
_STRUCTURED = re.compile(r"\((?:SO|CE)\s*\)|\bPTC\b|pass through|securiti[sz]", re.I)
_ST_FACILITY = re.compile(r"bank guarantee|letter of credit|\bLC\b|\bBG\b|short[- ]term", re.I)
_LT_FACILITY = re.compile(r"cash credit|term loan|working capital|long[- ]term|\bfund based", re.I)
_PVT = re.compile(r"\bprivate limited\b|\bpvt\.? ltd\b|\bpvt\b", re.I)


def instrument_for(signals: list[dict], is_lender: bool) -> tuple[str, str]:
    """(code, detail): code is one of INSTRUMENTS (spec 2), detail says why."""
    text = " ".join(f"{s.get('detail', '')} {s.get('instrument', '')} {s.get('rating', '')}"
                    for s in signals)
    kinds = {s["kind"] for s in signals}
    if "debt_raise" in kinds:
        if re.search(r"commercial paper|\bCPs?\b", text, re.I):
            return "CP", "issuing commercial paper"
        if _STRUCTURED.search(text):
            return "Securitisation/PTC", "structured / securitised issue"
        return "NCD", "issuing NCDs / bonds"
    if "refinance" in kinds:
        return "NCD", "rating for the refinancing issue"
    if _STRUCTURED.search(text):
        return "Securitisation/PTC", "rated on a structured (SO/CE) instrument"
    inst = next((s.get("instrument") for s in signals if s.get("instrument")), "")
    if inst:
        if _ST_FACILITY.search(inst):
            return "BLR-ST", f"rated on {inst}"
        if _LT_FACILITY.search(inst):
            return "BLR-LT", f"rated on {inst}"
        return "Other", f"rated on {inst}"
    if _SHORT_TERM.search(text):
        return ("CP" if is_lender else "BLR-ST"), "carries a short-term (A1-A4) rating"
    if is_lender:
        return "NCD", "lenders fund through NCDs / CP"
    return "BLR-LT", "bank facilities are the usual first rating"


def _play(trigger: str, code: str) -> str:
    return {
        "debt_raise": f"Reach them before the issue is placed - offer a quick-TAT {code} rating.",
        "refinance": "Their listed debt matures soon: pitch the rating for the rollover issue now.",
        "rating_action": f"Just rated by another agency - offer ACER as a second opinion on the {code}.",
        "thin_coverage": "Thin interest cover under macro pressure - lead with the credibility an independent rating gives lenders.",
        "capex": f"New capex needs bank funding - offer the {code} rating before the facility is sanctioned.",
        "deal": f"The deal brings new borrowing - offer the {code} rating for the funding.",
        "macro": f"Their sector just took a macro hit - open with a {code} rating as a lender-comfort tool.",
    }.get(trigger, f"Open with the {code} rating and ACER's turnaround time.")


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt, n in (("%d-%m-%Y", 10), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(s[:n], fmt).date()
        except ValueError:
            continue
    return None


def urgency(signals: list[dict], today: date | None = None) -> str:
    """Spec 2: This week if a debt raise is live, a maturity is <= 60 days
    away, or a rating action is <= 14 days old; else This month."""
    today = today or date.today()
    for s in signals:
        d = _parse_date(s.get("event_date") or s.get("read_at", ""))
        if s["kind"] == "debt_raise" and d and (today - d).days <= 14:
            return "This week"
        if s["kind"] == "rating_action" and d and (today - d).days <= 14:
            return "This week"
        if s["kind"] == "refinance":
            latest = _parse_date(s.get("maturity_latest", ""))
            if latest and (latest - today).days <= 60:
                return "This week"
    return "This month"


def segment_for(sectors: list[str], name: str, bds: list[dict]) -> tuple[str, str]:
    """(bd_id or "", label). Name-inferred, so the label says so."""
    for b in bds:
        if set(b.get("sectors", [])) & set(sectors):
            return b["id"], b.get("segment", b["name"])
    if not sectors and _PVT.search(name):
        for b in bds:
            if "sme" in b.get("sectors", []):
                return b["id"], b.get("segment", b["name"])
    return "", "Unclassified"


_RUPEES = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(lakh crore|crore|cr\b)", re.I)


def size_for(signals: list[dict]) -> str:
    """Rs crore stated in the filing text, or the gap labelled. Never estimated."""
    best = None
    for s in signals:
        for num, unit in _RUPEES.findall(f"{s.get('detail', '')} {s.get('text', '')}"):
            try:
                v = float(num.replace(",", "")) * (100000 if unit.lower().startswith("lakh") else 1)
            except ValueError:
                continue
            best = v if best is None else max(best, v)
    return f"~Rs {best:,.0f} cr (stated in the filing/article)" if best else "size not known"


# -- assembly (pure) ------------------------------------------------------------------

def build_rows(companies: dict[str, dict], exclude: set[str], bds: list[dict],
               prev: dict | None = None, inputs: dict | None = None,
               today: date | None = None, per_bd: int = PER_BD) -> tuple[list[dict], dict]:
    """Rank, derive every spec field, and split. Returns (rows, fill) where
    fill says per BD how many of `per_bd` were filled and why short.

    `companies`: norm name -> {name, cin, winnability, blocked, is_lender,
    win_reason, contact, acer_history, agency, latest_rating, signals: [...]}.
    `prev`: last month's snapshot (rows + outcomes) for rollover."""
    inputs = inputs or {}
    refi_down = inputs.get("refinance", {}).get("status") not in (None, "ok")
    prev_rows = {r["key"]: r for r in (prev or {}).get("rows", [])}
    prev_out = (prev or {}).get("outcomes") or {}

    candidates = []
    carried_in = []
    for key, c in companies.items():
        if key in exclude or c.get("blocked"):
            continue
        p = prev_rows.get(key)
        outcome = prev_out.get(p["company_name"], {}) if p else {}
        stage = outcome.get("stage") if isinstance(outcome, dict) else outcome
        if p and stage in IN_PROGRESS:
            carried_in.append((key, c, p, stage))       # stays with its owner, no slot
            continue
        if not c["signals"]:
            continue
        kinds = {s["kind"] for s in c["signals"]}
        score = (c.get("winnability") or 0) + sum(_BONUS.get(k, 0) for k in kinds)
        if score < MIN_SCORE:
            continue
        candidates.append((key, c, score, p))

    candidates.sort(key=lambda t: (-t[2], t[1]["name"]))

    def make_row(key, c, score, bd, segment_label, carried=False, in_progress=None):
        kinds = {s["kind"] for s in c["signals"]}
        trigger = next((t for t in TRIGGERS if t in kinds), "macro")
        code, why_inst = instrument_for(c["signals"], c.get("is_lender", False))
        why = "; ".join(dict.fromkeys(s["text"] for s in c["signals"])) or "carried from last month"
        win = c.get("win_reason") or ("winnability unknown - not in the CRA archive yet"
                                      if c.get("winnability") is None else "")
        maturity = next((s["text"] for s in c["signals"] if s["kind"] == "refinance"), "")
        return {
            "company_name": c["name"], "key": key,
            "cin": c.get("cin") or "CIN not found",
            "segment": segment_label, "segment_inferred": True,
            "instrument": code, "instrument_detail": why_inst,
            "trigger": trigger, "signals": sorted(kinds),
            "reason": f"{c['name']}: {why}" + (f" ({win})" if win else "") + ".",
            "play": _play(trigger, code),
            "sources": [{"label": s["source"], "url": database.safe_url(s.get("url", "")),
                         "read_at": s.get("read_at", "")} for s in c["signals"]],
            "score": score, "winnability": c.get("winnability"),
            "urgency": urgency(c["signals"], today),
            "contact_route": c.get("contact") or "not sourced - BD network or via lender/banker",
            "size_cr": size_for(c["signals"]),
            "current_agency": c.get("agency") or NOT_VISIBLE,
            "latest_rating": c.get("latest_rating") or NOT_VISIBLE,
            "maturity_date": maturity or ("BSE unavailable" if refi_down else "none listed within 9 months"),
            "acer_history": c.get("acer_history") or "No ACER history",
            "carried_over": carried, "in_progress": in_progress,
            "bd_id": bd["id"], "bd_name": bd["name"],
        }

    by_id = {b["id"]: b for b in bds}
    rows: list[dict] = []
    filled = {b["id"]: 0 for b in bds}
    pool = []

    # 1. Segment owners take their own names, best first.
    for key, c, score, p in candidates:
        sectors = nc.sectors_for(c["name"]) + (["nbfc"] if c.get("is_lender") else [])
        owner, label = segment_for(sectors, c["name"], bds)
        untouched_before = p is not None
        if owner and not untouched_before and filled[owner] < per_bd:
            rows.append(make_row(key, c, score, by_id[owner], label))
            filled[owner] += 1
        elif not owner or untouched_before:
            pool.append((key, c, score, p, label))
        # a segment's overflow is not pushed onto another segment's BD (spec 1)

    # 2. The pool fills anyone still short, snake order; a name one BD left
    # untouched last month goes to a different BD (spec 5).
    order = list(bds)
    rnd = 0
    while pool and any(filled[b["id"]] < per_bd for b in bds):
        seq = order if rnd % 2 == 0 else list(reversed(order))
        progressed = False
        for b in seq:
            if filled[b["id"]] >= per_bd:
                continue
            pick = next((i for i, (k, c, s, p, l) in enumerate(pool)
                         if not (p and p.get("bd_id") == b["id"])), None)
            if pick is None:
                continue
            key, c, score, p, label = pool.pop(pick)
            rows.append(make_row(key, c, score, b, label, carried=p is not None))
            filled[b["id"]] += 1
            progressed = True
            if not pool:
                break
        if not progressed:
            break
        rnd += 1

    # 3. Last month's names still being worked stay with their owner.
    for key, c, p, stage in carried_in:
        bd = by_id.get(p.get("bd_id"))
        if bd:
            c = c if c["signals"] else dict(c, signals=[{
                "kind": p.get("trigger", "macro"), "text": "still being worked from last month",
                "source": f"BD list {prev.get('month', '')}", "url": "", "read_at": ""}])
            rows.append(make_row(key, c, p.get("score", 0), bd, p.get("segment", ""),
                                 in_progress=stage))

    # Rank within each BD, best first; in-progress names after new ones.
    rows.sort(key=lambda r: (r["bd_id"], r["in_progress"] is not None, -r["score"], r["company_name"]))
    for b in bds:
        for i, r in enumerate(x for x in rows if x["bd_id"] == b["id"]):
            r["rank"] = i + 1
    fill = {b["id"]: {"filled": filled[b["id"]], "of": per_bd,
                      "note": "" if filled[b["id"]] >= per_bd else
                      f"{filled[b['id']]} of {per_bd}: segment thin this month"}
            for b in bds}
    return rows, fill


# -- inputs (network) -----------------------------------------------------------------

def _is_lender(name: str) -> bool:
    return bool(set(nc.sectors_for(name)) & {"nbfc"})


def _registry_index() -> dict:
    """CIN, entity type and RBI-filed email by exact (folded) name - one read
    of the registry per build (PREMORTEM section 5: exact match only)."""
    try:
        from backend.registry import store
        return store.exact_index()
    except Exception as e:
        log.warning("registry index unavailable: %s", e)
        return {}


def _history() -> tuple[dict, bool]:
    """norm name -> ACER's history with the company, across every BD's
    pipeline (service key). (history, readable)."""
    from backend.pipeline import pipeline_store
    if not pipeline_store.per_user():
        # No Supabase at all (local dev): the SQLite pipeline is the whole store.
        saved = pipeline_store._local_list_leads()
        events, ok, ok2 = pipeline_store._local_events(None, 5000), True, True
    else:
        ok, saved = database.remote("bd_list history saved", lambda c: database.select_all(
            c, "saved_leads", "company_name,user_id,stage,owner", ("user_id", "company_name")))
        ok2, events = database.remote("bd_list history events", lambda c: database.select_all(
            c, "lead_events", "id,company_name,event,detail,at", ("id",)))
    if not ok:
        return {}, False
    out: dict[str, str] = {}
    for e in (events if ok2 else []) or []:
        if e.get("event") == "stage" and "-> Lost" in (e.get("detail") or ""):
            out[norm_name(e["company_name"])] = f"Lost before ({(e.get('at') or '')[:10]}): {e['detail']}"
    for s in saved or []:
        who = s.get("owner") or "a BD"
        out[norm_name(s["company_name"])] = f"In {who}'s pipeline at {s['stage']}"
    return out, True


async def _gather() -> tuple[dict, dict]:
    """Every input, degrading independently. Returns (companies, inputs)."""
    from backend.pipeline import macro as macro_mod
    from backend.pipeline.lead_queue import build_queue

    by: dict[str, dict] = {}
    inputs: dict[str, dict] = {}

    def entry(name: str) -> dict:
        key = norm_name(name)
        e = by.get(key)
        if e is None:
            e = by[key] = {"name": name, "winnability": None, "blocked": False,
                           "is_lender": _is_lender(name), "win_reason": "", "signals": [],
                           "agency": "", "latest_rating": ""}
        return e

    queue, macro, news_rows, hist = await asyncio.gather(
        build_queue(days=30, enrich=15), macro_mod.build(14),
        asyncio.to_thread(news_archive.since, 30), asyncio.to_thread(_history),
        return_exceptions=True)

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
            e["agency"] = ", ".join(r.get("agencies_seen", []))
            e["latest_rating"] = r.get("latest_rating", "")
            e["signals"].append({
                "kind": "rating_action", "rating": r.get("latest_rating", ""),
                "event_date": r.get("latest_date", ""),
                "text": f"{r.get('latest_action', 'rating action')} at "
                        f"{'/'.join(r.get('agencies_seen', [])) or 'another agency'} "
                        f"({r.get('latest_rating', '')}, {r.get('latest_date', '')})",
                "source": f"{'/'.join(r.get('agencies_seen', []))} press release",
                "url": r.get("source_url", ""), "read_at": r.get("latest_date", "")})

    if isinstance(macro, Exception):
        inputs["macro"] = {"status": "unreachable", "detail": str(macro)[:200]}
        inputs["refinance"] = {"status": "unreachable", "detail": "macro build failed"}
    else:
        stale = macro.get("coverage", {}).get("stale_inputs", [])
        inputs["macro"] = {"status": "stale" if stale else "ok", "stale_inputs": stale}
        inputs["refinance"] = ({"status": "unreachable", "detail": "BSE blocks cloud servers"}
                               if "BSE scrip master" in stale else {"status": "ok"})
        for block in macro.get("sectors", []):
            for c in block["companies"]:
                e = entry(c["company_name"])
                if c.get("winnability") is not None and e["winnability"] is None:
                    e["winnability"] = c["winnability"]
                e["blocked"] = e["blocked"] or bool(c.get("blocked"))
                for t in c["triggers"]:
                    kind = "refinance" if t["type"] == "refinance" else "thin_coverage"
                    e["signals"].append({"kind": kind, "text": t["text"], "source": t["source"],
                                         "url": t.get("url", ""), "read_at": t.get("read_at", ""),
                                         "maturity_latest": t.get("maturity_latest", "")})
                ev = block["events"][0] if block["events"] else None
                if ev:
                    e["signals"].append({
                        "kind": "macro",
                        "text": f"in {block['sector_label']}, hit by \"{ev['subject'][:80]}\"",
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
                "event_date": r.get("date", ""),
                "text": f"{r['source']} filing: {r.get('subject', '')[:90]}",
                "source": f"{r['source']} filing", "url": r.get("link", ""),
                "read_at": r.get("first_seen", "")})

    if isinstance(hist, Exception) or not hist[1]:
        inputs["pipeline"] = {"status": "unreachable",
                              "detail": "ACER history not visible - saved_leads unreadable"}
        history = {}
    else:
        inputs["pipeline"] = {"status": "ok"}
        history = hist[0]

    reg_index = await asyncio.to_thread(_registry_index)
    for key, e in by.items():
        reg = reg_index.get(key, {})
        e["cin"] = reg.get("cin", "")
        if reg.get("entity_type") in ("NBFC", "ARC"):
            e["is_lender"] = True
        if reg.get("email") and e["is_lender"]:
            e["contact"] = f"RBI-registry email: {reg['email']}"
        e["acer_history"] = history.get(key) or (
            "No ACER history" if inputs["pipeline"]["status"] == "ok"
            else "ACER history not visible (pipeline unreadable)")
    return by, inputs


# -- snapshot store ---------------------------------------------------------------------

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
            return {"month": month, "rows": [], "frozen": False, "fill": {},
                    "inputs": {}, "note": "No list was generated for that month."}

        # Close the previous month first, so its outcomes are recorded from
        # the pipeline as it stood when the month ended.
        prev = await asyncio.to_thread(load, _prev_month(month))
        if prev and not prev.get("outcomes"):
            prev = await asyncio.to_thread(record_outcomes, prev)

        by, inputs = await _gather()
        rows, fill = build_rows(by, set(acer_book.client_names()), roster(), prev, inputs)
        snap = {"month": month, "version": (existing or {}).get("version", 0) + 1,
                "generated_at": _now_iso(), "inputs": inputs | {"_fill": fill},
                "rows": rows, "outcomes": None}
        store = await asyncio.to_thread(_store, snap)
        return snap | {"frozen": True, "store": store, "durable": store == "supabase"}


def _stage_rank(stage: str) -> int:
    return STAGES.index(stage) if stage in STAGES else -1


def outcomes_from(rows: list[dict], saved: list[dict], events: list[dict],
                  month: str) -> dict:
    """Spec 5: outcome = highest stage reached by month end (from stage
    events during the month, else the current stage), plus `touched`."""
    y, m = map(int, month.split("-"))
    start = date(y, m, 1).isoformat()
    end = (date(y + (m == 12), m % 12 + 1, 1)).isoformat()
    reached: dict[str, int] = {}
    for e in events or []:
        at = (e.get("at") or "")[:10]
        if e.get("event") != "stage" or not (start <= at < end):
            continue
        to = (e.get("detail") or "").split("->")[-1].split("|")[0].strip()
        k = norm_name(e.get("company_name", ""))
        reached[k] = max(reached.get(k, -1), _stage_rank(to))
    current = {norm_name(s["company_name"]): s for s in saved or []}
    out = {}
    for r in rows:
        k = r["key"]
        best = max(reached.get(k, -1), _stage_rank(current.get(k, {}).get("stage", "")))
        stage = STAGES[best] if best >= 0 else "not worked"
        out[r["company_name"]] = {"stage": stage, "touched": best > 0,
                                  "bd_id": r["bd_id"], "bd_name": r["bd_name"]}
    return out


def record_outcomes(snap: dict) -> dict:
    """Month-end: freeze each assigned name's outcome into the snapshot, and
    append month_end rows to lead_events for names somebody saved (the event
    needs the saver's user id). Needs the service key to read every BD's
    pipeline; without it, outcomes cannot be seen and are not faked."""
    from backend.pipeline import pipeline_store
    local = not pipeline_store.per_user()
    if local:
        saved, ok = pipeline_store._local_list_leads(), True
        events, ok2 = pipeline_store._local_events(None, 5000), True
    else:
        ok, saved = database.remote("bd_list outcomes read", lambda c: database.select_all(
            c, "saved_leads", "company_name,user_id,stage", ("user_id", "company_name")))
        ok2, events = (database.remote("bd_list outcomes events read", lambda c: database.select_all(
            c, "lead_events", "id,company_name,event,detail,at", ("id",))) if ok else (False, []))
    if not ok:
        log.warning("bd_list: month-end outcomes for %s not recorded - saved_leads unreadable",
                    snap.get("month"))
        return snap
    outcomes = outcomes_from(snap.get("rows", []), saved, events if ok2 else [], snap["month"])
    by_name = {norm_name(s["company_name"]): s for s in saved or []}
    rows_ev = [{"company_name": by_name[r["key"]]["company_name"],
                "user_id": by_name[r["key"]]["user_id"], "event": "month_end",
                "detail": f"BD list {snap['month']} ({r['bd_name']}): "
                          f"{outcomes[r['company_name']]['stage']}"}
               for r in snap.get("rows", []) if r["key"] in by_name]
    if rows_ev and not local:
        database.remote("bd_list outcomes events",
                        lambda c: c.table("lead_events").insert(rows_ev).execute())
    elif local:
        with pipeline_store._connect() as con:
            for r in snap.get("rows", []):
                if r["key"] in by_name:
                    pipeline_store._event(con, by_name[r["key"]]["company_name"], "month_end",
                                          f"BD list {snap['month']} ({r['bd_name']}): "
                                          f"{outcomes[r['company_name']]['stage']}")
    snap = snap | {"outcomes": outcomes}
    _store(snap)
    return snap


# -- self-check (no network) -------------------------------------------------------------

def _demo() -> None:
    bds = roster()
    assert [b["name"] for b in bds] == ["Hema", "Avinash", "Akash", "Udit"], bds
    today = date(2026, 9, 28)

    def co(name, win, kinds, lender=False, blocked=False, **extra):
        return {"name": name, "winnability": win, "blocked": blocked, "is_lender": lender,
                "win_reason": "", "cin": "", "signals": [dict({
                    "kind": k, "text": f"{k} signal", "source": "src", "url": "https://s",
                    "read_at": "2026-09-27"}, **extra) for k in kinds]}

    data = {}
    for i in range(12):
        data[norm_name(f"Alpha Finance {i} Ltd")] = co(f"Alpha Finance {i} Ltd", 30 + i, ["rating_action"], lender=True)
    data[norm_name("Beta Steel Ltd")] = co("Beta Steel Ltd", 40, ["debt_raise"],
                                           detail="Allotment of NCDs Rs 250 crore",
                                           event_date="2026-09-25")
    data[norm_name("Gamma Realty Ltd")] = co("Gamma Realty Ltd", 20, ["thin_coverage"])
    data[norm_name("Delta Traders Private Limited")] = co("Delta Traders Private Limited", 25,
                                                          ["rating_action"], rating="ACUITE A4",
                                                          event_date="01-08-2026")
    data[norm_name("Omega Widgets Ltd")] = co("Omega Widgets Ltd", 30, ["rating_action"])
    data[norm_name("Weak Ltd")] = co("Weak Ltd", 0, ["macro"])                  # below the floor
    data[norm_name("Bad Finance Ltd")] = co("Bad Finance Ltd", 90, ["rating_action"], blocked=True)
    data[norm_name("Client Steel Ltd")] = co("Client Steel Ltd", 90, ["debt_raise"])

    rows, fill = build_rows(data, {norm_name("Client Steel Ltd")}, bds, today=today,
                            inputs={"refinance": {"status": "unreachable"}})
    by = {r["company_name"]: r for r in rows}
    names = set(by)
    # Never listed: below the floor, blocked, our client.
    assert not names & {"Weak Ltd", "Bad Finance Ltd", "Client Steel Ltd"}, names
    # Segment first: finance names to Hema (10 max, no overflow onto others).
    hema = [r for r in rows if r["bd_id"] == "hema"]
    assert len(hema) == 10 and all("Finance" in r["company_name"] for r in hema), hema
    assert by["Beta Steel Ltd"]["bd_id"] == "avinash"
    assert by["Gamma Realty Ltd"]["bd_id"] == "akash"
    assert by["Delta Traders Private Limited"]["bd_id"] == "udit"
    # Unclassified goes to the pool and fills someone short - not Hema (full).
    assert by["Omega Widgets Ltd"]["segment"] == "Unclassified"
    assert by["Omega Widgets Ltd"]["bd_id"] != "hema"
    # Never padded: thin segments say so.
    assert fill["avinash"]["note"].endswith("segment thin this month"), fill
    # Spec 2 fields, gaps labelled, never blank.
    b = by["Beta Steel Ltd"]
    assert b["instrument"] == "NCD" and b["trigger"] == "debt_raise", b
    assert b["urgency"] == "This week" and b["size_cr"].startswith("~Rs 250"), b
    assert b["cin"] == "CIN not found" and b["current_agency"] == NOT_VISIBLE
    assert b["maturity_date"] == "BSE unavailable"
    assert by["Delta Traders Private Limited"]["instrument"] == "BLR-ST"
    assert by["Delta Traders Private Limited"]["urgency"] == "This month"
    for r in rows:
        for f in ("company_name", "cin", "segment", "instrument", "trigger", "reason", "play",
                  "sources", "score", "urgency", "contact_route", "rank"):
            assert r[f] not in (None, "", []), (f, r)
        assert r["instrument"] in INSTRUMENTS and r["trigger"] in TRIGGERS
    # Deterministic.
    assert build_rows(data, {norm_name("Client Steel Ltd")}, bds, today=today,
                      inputs={"refinance": {"status": "unreachable"}}) == (rows, fill)

    # Rollover: an in-progress name stays with its owner outside the 10; an
    # untouched one goes to a different BD, marked carried_over.
    prev = {"month": "2026-08", "rows": [dict(by["Gamma Realty Ltd"]), dict(by["Beta Steel Ltd"])],
            "outcomes": {"Gamma Realty Ltd": {"stage": "Meeting"},
                         "Beta Steel Ltd": {"stage": "not worked"}}}
    rows2, _ = build_rows(data, set(), bds, prev=prev, today=today)
    g = [r for r in rows2 if r["company_name"] == "Gamma Realty Ltd"][0]
    assert g["bd_id"] == "akash" and g["in_progress"] == "Meeting", g
    bs = [r for r in rows2 if r["company_name"] == "Beta Steel Ltd"][0]
    assert bs["carried_over"] and bs["bd_id"] != "avinash", bs

    # Month-end outcome = highest stage reached during the month.
    out = outcomes_from([by["Beta Steel Ltd"]],
                        [{"company_name": "Beta Steel Ltd", "stage": "Lost"}],
                        [{"company_name": "Beta Steel Ltd", "event": "stage",
                          "detail": "Contacted -> Proposal", "at": "2026-09-20T10:00:00"}],
                        "2026-09")
    assert out["Beta Steel Ltd"]["stage"] == "Lost" and out["Beta Steel Ltd"]["touched"], out

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
            assert load("2026-09")["rows"] == rows
            _store(snap | {"version": 2, "rows": rows[:1]})
            assert load("2026-09")["version"] == 2 and load("2026-08") is None
            # Without a readable pipeline, outcomes are not invented.
            from backend.pipeline import pipeline_store as _ps
            real_pu = _ps.per_user
            _ps.per_user = lambda: True
            try:
                assert record_outcomes(load("2026-09")).get("outcomes") is None
            finally:
                _ps.per_user = real_pu
        finally:
            DB_PATH, database.get_client = real, real_client
    assert _prev_month("2026-01") == "2025-12"
    print("bd_list self-check: ok")


if __name__ == "__main__":
    _demo()
