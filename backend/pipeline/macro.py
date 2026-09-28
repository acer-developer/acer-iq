"""
MACRO - a big thing happened: who does it hit, and do they now need a rating?
(BUILD_PLAN Phase 3, Tab 1.)

A join, not a model:

    major macro event (news_archive + news_classify)
      -> its sectors
      -> companies in those sectors with
            debt maturing inside 9 months   (refinance.py, BSE scrip master)
         OR thin interest coverage (< 2x)   (fundamentals.py, NSE XBRL)
      -> ranked by existing winnability     (winnability.py over the CRA archive)
      -> a named list, each name with a reason and a source

No new data source, no ML. Every company row carries the source it came from
and the date that source was read, and a sentence saying why it is on the list
(BUILD_PLAN invariants 2 and 3). ACER's own clients are never listed.

Honest limits, stated in every response (`coverage`):

  * Sector comes from the company name, the RBI registry's entity type (exact
    name match only, PREMORTEM section 5) and NSE's bank flag.
    ponytail: a company whose name does not say its industry ("Bharat Forge")
    is invisible here. Upgrade path is an industry master (NSE's quote API
    `industryInfo`, or BSE's industry field) cached alongside the scrip master.
  * Thin coverage is checked for at most `_XBRL_BUDGET` companies per build,
    because each is one XBRL download from NSE; the rest are not "fine", they
    are unchecked, and the response says how many.
  * Winnability is known only for issuers already in the CRA action archive.
    Everyone else is shown with winnability unknown, never a guessed score.

Self-check (no network):  python -m backend.pipeline.macro
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from backend.pipeline import acer_book, action_history, news_archive, winnability
from backend.pipeline import news_classify as nc
from backend.pipeline.pipeline_store import norm_name
from backend.pipeline.ttl_cache import TTLCache

log = logging.getLogger("acer-iq.macro")

THIN_COVERAGE = 2.0      # fundamentals.summarise calls < 2x "thin"
# A lender's interest cost IS its cost of goods: NBFCs normally run 1.3-1.8x,
# so 2x would flag every one of them. Below 1.25x is where a lender's margin
# stops covering its funding cost.
# ponytail: a judgement line, not fitted - tune once lead_events holds outcomes.
THIN_COVERAGE_LENDER = 1.25
REFINANCE_MONTHS = 9     # BUILD_PLAN: debt maturing < 9 months
_XBRL_BUDGET = 30        # XBRL downloads per build, across all sectors
_PER_SECTOR = 12         # names shown per sector

_cache = TTLCache(ttl=1800, maxsize=8)

# RBI registry entity type -> sector key
_ENTITY_SECTOR = {"NBFC": "nbfc", "ARC": "nbfc", "Bank": "banking"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def company_sectors(name: str, entity_type: str = "", is_bank: bool = False) -> list[str]:
    """Sector keys for one company, from its name and what the registry says it is."""
    keys = nc.sectors_for(name)
    extra = _ENTITY_SECTOR.get(entity_type)
    if extra and extra not in keys:
        keys.append(extra)
    if is_bank and "banking" not in keys:
        keys.append("banking")
    return keys


_REG_INDEX: dict = {}


def _registry_type(name: str) -> str:
    """Entity type from the RBI registry - exact (folded) name only. A loose
    match would tag REC Power Development as REC Ltd (PREMORTEM section 5).
    One registry read per process, not one LIKE scan per name."""
    global _REG_INDEX
    if not _REG_INDEX:
        try:
            from backend.registry import store
            _REG_INDEX = store.exact_index()
        except Exception:
            return ""
    return _REG_INDEX.get(norm_name(name), {}).get("entity_type", "")


def _winnability_index() -> dict[str, dict]:
    """norm name -> winnability verdict, from the CRA action archive."""
    from backend.pipeline.lead_queue import _credit_data_for
    grouped: dict[str, list[dict]] = {}
    for a in action_history.since(365):
        k = norm_name(a.get("company_name", ""))
        if k:
            grouped.setdefault(k, []).append(a)
    return {k: winnability.score({"name": k}, _credit_data_for(v)) for k, v in grouped.items()}


def macro_events(rows: list[dict]) -> list[dict]:
    """Archived news rows that are major macro events, newest first."""
    out = []
    for r in rows:
        c = nc.classify(r)
        if c["macro"]:
            out.append({"subject": r["subject"], "source": r["source"],
                        "link": r.get("link", ""), "published": r.get("date", ""),
                        "read_at": r.get("first_seen", ""), "sectors": c["sectors"],
                        "sector_labels": c["sector_labels"], "why": c["why"]})
    return out


def join(events: list[dict], refinance_rows: list[dict], coverage_rows: list[dict],
         win_index: dict[str, dict], exclude: set[str],
         registry_type=lambda name: "") -> list[dict]:
    """The Phase 3 join. Pure - no I/O - so the self-check exercises it.

    `refinance_rows`: {issuer_name, maturity_label, instrument_count,
    source_url, read_at}. `coverage_rows`: {company_name, interest_coverage,
    verdict, source_url, read_at, is_bank}. Returns one block per hit sector."""
    by_sector: dict[str, list[dict]] = {}
    for ev in events:
        for s in ev["sectors"]:
            by_sector.setdefault(s, []).append(ev)

    # name -> candidate, merging the two triggers for one company
    cands: dict[str, dict] = {}

    def cand(name: str, entity: str = "", is_bank: bool = False) -> dict | None:
        key = norm_name(name)
        if not key or key in exclude:
            return None
        c = cands.get(key)
        if c is None:
            c = cands[key] = {"name": name, "key": key, "triggers": [],
                              "sectors": company_sectors(name, entity or registry_type(name), is_bank)}
        return c

    for r in refinance_rows:
        c = cand(r["issuer_name"])
        if c is not None:
            n = r.get("instrument_count") or 1
            c["triggers"].append({
                "type": "refinance",
                "text": f"debt maturing {r['maturity_label']} ({n} listed instrument{'s' if n != 1 else ''})",
                "source": "BSE active debt scrip master", "url": r.get("source_url", ""),
                "read_at": r.get("read_at", ""), "maturity_latest": r.get("maturity_latest", "")})
    for r in coverage_rows:
        ic = r.get("interest_coverage")
        if ic is None or r.get("is_bank"):
            continue    # EBIT / finance costs says nothing about a bank
        c = cand(r["company_name"])
        if c is None:
            continue
        line = THIN_COVERAGE_LENDER if "nbfc" in c["sectors"] else THIN_COVERAGE
        if ic >= line:
            continue
        c["triggers"].append({
            "type": "thin_coverage", "text": r["verdict"].lower(),
            "source": f"NSE results filing ({r.get('period', '')})".replace(" ()", ""),
            "url": r.get("source_url", ""), "read_at": r.get("read_at", "")})

    blocks = []
    for sector, evs in sorted(by_sector.items(), key=lambda kv: -len(kv[1])):
        names = []
        for c in cands.values():
            if sector not in c["sectors"] or not c["triggers"]:
                continue
            w = win_index.get(c["key"])
            ev = evs[0]
            trig = " and ".join(t["text"] for t in c["triggers"])
            if w:
                win_txt = f"winnability {w['winnability']} ({w['label'].lower()}): {w['reasons'][0]}"
                if w.get("blocked"):
                    win_txt += f" - BLOCKED: {w['credit_screen']['reason']}"
            else:
                win_txt = ("winnability unknown - no rating action of theirs in the CRA archive yet, "
                           "check coverage before calling")
            names.append({
                "company_name": c["name"],
                "triggers": c["triggers"],
                "winnability": w["winnability"] if w else None,
                "winnability_label": w["label"] if w else "Unknown",
                "blocked": bool(w and w.get("blocked")),
                "reason": (f"{c['name']} is in {nc.sector_label(sector)}, which "
                           f"\"{ev['subject'][:90]}\" hits, and has {trig}; {win_txt}."),
            })
        # Rank: winnable first, blocked last, unknown in between; then the
        # sharper trigger (coverage below 1x, then both triggers at once).
        def rank(n):
            ic = min((t for t in n["triggers"] if t["type"] == "thin_coverage"),
                     key=lambda t: t["text"], default=None)
            return (n["blocked"], -(n["winnability"] if n["winnability"] is not None else -1),
                    -len(n["triggers"]), 0 if ic and "does not cover" in ic["text"] else 1,
                    n["company_name"])
        names.sort(key=rank)
        blocks.append({"sector": sector, "sector_label": nc.sector_label(sector),
                       "events": evs[:5], "event_count": len(evs),
                       "companies": names[:_PER_SECTOR], "company_count": len(names)})
    return blocks


async def _coverage_rows(sectors: set[str]) -> tuple[list[dict], dict]:
    """Interest coverage for NSE filers whose name places them in a hit sector,
    within the XBRL budget. Returns (rows, stats)."""
    from backend.pipeline import fundamentals
    try:
        index = await fundamentals._results_index()
    except Exception as e:
        log.warning("macro: NSE results index failed: %s: %s", type(e).__name__, e)
        index = {}
    if not index:
        return [], {"status": "unverified", "checked": 0, "in_sector": 0}

    pool = []
    for sym, rec in index.items():
        name = rec.get("companyName", "")
        secs = company_sectors(name, is_bank=rec.get("bank") == "Y")
        if sectors.intersection(secs):
            pool.append((sym, rec))
    pool.sort(key=lambda p: fundamentals._filed_at(p[1]), reverse=True)
    budget = pool[:_XBRL_BUDGET]
    # Two at a time: each XBRL is a multi-MB download parsed in memory, and
    # Render's free tier has 512 MB for the whole app.
    sem = asyncio.Semaphore(2)

    async def one(sym, rec):
        async with sem:
            f = await fundamentals.fetch_for_symbol(sym)
        if f.get("status") != "ok":
            return None
        return {"company_name": f.get("company_name") or rec.get("companyName", sym),
                "interest_coverage": f.get("interest_coverage"),
                "verdict": f.get("verdict", ""), "source_url": f.get("source_url", ""),
                "period": f"{f.get('period', '')} {f.get('financial_year', '')}".strip(),
                "read_at": _now_iso(), "is_bank": rec.get("bank") == "Y"}

    got = await asyncio.gather(*(one(s, r) for s, r in budget), return_exceptions=True)
    rows = [g for g in got if isinstance(g, dict)]
    return rows, {"status": "ok", "checked": len(budget), "in_sector": len(pool),
                  "unchecked": max(0, len(pool) - len(budget))}


async def _refinance_rows() -> tuple[list[dict], str]:
    from backend.pipeline import bse_scraper
    from backend.pipeline.refinance import find_refinance_candidates
    data = await find_refinance_candidates(months_ahead=REFINANCE_MONTHS)
    read_at = (datetime.fromtimestamp(bse_scraper._master_at, timezone.utc)
               .isoformat(timespec="seconds") if bse_scraper._master_at else "")
    rows = [{"issuer_name": c["issuer_name"], "maturity_label": c["maturity_label"],
             "instrument_count": c["instrument_count"],
             "maturity_latest": c.get("maturity_latest", ""),
             "source_url": bse_scraper.BSE_SCRIP_MASTER, "read_at": read_at}
            for c in data.get("candidates", [])]
    return rows, data.get("data_status", "unverified")


async def build(days: int = 14) -> dict:
    """Tab 1. Degrades rather than raises: a dead input is named in
    `coverage`, and the list is built from whatever did answer."""
    key = f"macro:{days}"
    hit = _cache.get(key)
    if hit is not None:
        return hit

    rows = await asyncio.to_thread(news_archive.since, days)
    events = macro_events(rows)
    sectors = {s for e in events for s in e["sectors"]}

    if sectors:
        (refi, refi_status), (cov, cov_stats) = await asyncio.gather(
            _refinance_rows(), _coverage_rows(sectors))
    else:
        refi, refi_status, cov, cov_stats = [], "not_needed", [], {"status": "not_needed"}

    win_index = await asyncio.to_thread(_winnability_index)
    blocks = await asyncio.to_thread(
        join, events, refi, cov, win_index, set(acer_book.client_names()), _registry_type)

    stale = [n for n, st in (("BSE scrip master", refi_status),
                             ("NSE results", cov_stats.get("status")))
             if st == "unverified"]
    out = {
        "window_days": days,
        "events": events[:50],
        "event_count": len(events),
        "sectors": blocks,
        "named": sum(len(b["companies"]) for b in blocks),
        "coverage": {
            "refinance": refi_status,
            "fundamentals": cov_stats,
            "stale_inputs": stale,
            "winnability_known_for": len(win_index),
            "note": ("Sector comes from company names and the RBI registry; a company "
                     "whose name does not say its industry is not matched. Interest "
                     f"coverage checked for {cov_stats.get('checked', 0)} of "
                     f"{cov_stats.get('in_sector', 0)} NSE filers in the hit sectors."),
        },
        "built_at": _now_iso(),
    }
    # Only a complete build is cached: caching one built on a dead input
    # would keep the gap on screen for half an hour after the input recovers.
    if not stale:
        _cache.set(key, out)
    return out


# -- self-check (no network) ---------------------------------------------------

def _demo() -> None:
    ev = macro_events([
        {"subject": "India 10-year yield hits two-year high as supply bites",
         "source": "Economic Times", "link": "https://et.example/y", "date": "2026-09-28",
         "first_seen": "2026-09-28T10:00:00+00:00", "company": "", "description": ""},
        {"subject": "Top 5 stocks to buy tomorrow", "source": "LiveMint", "link": "x",
         "date": "2026-09-28", "first_seen": "2026-09-28T10:00:00+00:00",
         "company": "", "description": ""},
    ])
    assert len(ev) == 1 and "nbfc" in ev[0]["sectors"], ev

    refi = [{"issuer_name": "Acme Finance Limited", "maturity_label": "during 2027",
             "instrument_count": 2, "source_url": "https://bse.example", "read_at": "2026-09-28"},
            {"issuer_name": "Our Client Finance Limited", "maturity_label": "during 2027",
             "instrument_count": 1, "source_url": "u", "read_at": "r"},
            {"issuer_name": "Bharat Widgets Limited", "maturity_label": "during 2027",
             "instrument_count": 1, "source_url": "u", "read_at": "r"}]
    cov = [{"company_name": "Beta Housing Finance Limited", "interest_coverage": 0.8,
            "verdict": "Interest coverage 0.8x - operating profit does not cover interest",
            "source_url": "https://nse.example/xbrl", "read_at": "2026-09-28", "period": "Q1"},
           {"company_name": "Gamma Finance Limited", "interest_coverage": 3.1,
            "verdict": "Interest coverage 3.1x", "source_url": "u", "read_at": "r"}]
    win = {norm_name("Acme Finance Limited"): winnability.score(
        {"name": "x"}, {"rating_actions": [{"action": "Rating withdrawn at the issuer's request",
                                             "rating": "A"}],
                        "agencies": [], "rated_by_count": 1, "data_status": "ok"})}
    blocks = join(ev, refi, cov, win, exclude={norm_name("Our Client Finance Limited")})
    nbfc = [b for b in blocks if b["sector"] == "nbfc"][0]
    names = [c["company_name"] for c in nbfc["companies"]]
    # Known-winnable first, then unknown; comfortable coverage and our own
    # client never appear; a name with no sector word is not matched.
    assert names == ["Acme Finance Limited", "Beta Housing Finance Limited"], names
    # A lender at 1.6x is normal, not thin; a bank's coverage is never used.
    ok_lender = [{"company_name": "Delta Finance Limited", "interest_coverage": 1.6,
                  "verdict": "Interest coverage 1.6x - thin", "source_url": "u", "read_at": "r"},
                 {"company_name": "Epsilon Bank Limited", "interest_coverage": 0.9,
                  "verdict": "x", "source_url": "u", "read_at": "r", "is_bank": True}]
    assert not [c for b in join(ev, [], ok_lender, {}, set()) for c in b["companies"]]
    acme, beta = nbfc["companies"]
    assert acme["winnability"] == 25 and "withdrew" in acme["reason"].lower(), acme
    assert beta["winnability"] is None and "winnability unknown" in beta["reason"], beta
    # Every name carries a reason and a source with a read date.
    for c in nbfc["companies"]:
        assert c["reason"] and all(t["url"] and t["read_at"] for t in c["triggers"]), c
    assert "India 10-year yield" in acme["reason"]
    # No macro event -> nothing named, not everything named.
    assert join([], refi, cov, win, set()) == []
    print("macro self-check: ok")


if __name__ == "__main__":
    _demo()
