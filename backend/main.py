import asyncio
import csv
import io
import json
import logging
import uuid
from collections import Counter
from contextvars import ContextVar
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.models import (
    Company, Contact, Director, OfficeLocation, PastInstrument,
    SearchRequest, SearchResponse,
)
from backend.pipeline.discovery import discover_companies
from backend.pipeline.enricher import enrich_contacts
from backend.pipeline.mca_scraper import fetch_mca_data
from backend.pipeline.scorer import score_company
from backend.pipeline.bse_scraper import bse_health_check, fetch_past_instruments
from backend.pipeline.office_locator import find_office_locations
from backend.pipeline.credit_history import fetch_credit_history
from backend.pipeline.fit_analyzer import analyze_fit
from backend.pipeline import winnability as winnability_scorer
from backend.pipeline.lead_queue import build_queue
from backend.pipeline import pipeline_store
from backend.pipeline.briefing import build_briefing
from backend.pipeline import brief as pitch_brief
from backend.pipeline.refinance import find_refinance_candidates
from backend.pipeline.fundamentals import fetch_for_symbol as fetch_fundamentals
from backend.pipeline import acer_book
from backend.pipeline.market_news import fetch_market_news
from backend.pipeline.rss_news import fetch_rss_news
from backend.pipeline.sector_indices import fetch_sector_indices
from backend import database
from backend.config import settings
from backend.registry import store as registry_store

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
log = logging.getLogger("acer-iq")

app = FastAPI(title="ACER-IQ", version="3.1.0")

# Vercel frontend (incl. preview deploys) + local dev. Override with
# ALLOWED_ORIGINS=https://a.example,https://b.example to pin exact hosts.
_origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=(
        None if _origins
        else r"https://[\w.-]+\.vercel\.app|http://localhost:\d+|http://127\.0\.0\.1:\d+"
    ),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_search_cache: dict[str, list[dict]] = {}

# Per-request tally of which enrichment sources failed, so the API can tell the
# UI "BSE returned nothing because it errored" instead of showing a silent blank.
# A Counter object shared through the context survives asyncio.gather (each Task
# copies the context, but the Counter it points at is the same object).
_failures: ContextVar[Counter | None] = ContextVar("failures", default=None)


async def _safe(coro, default, label: str = ""):
    """Run a coroutine, returning default on failure. Never silent: every
    failure is logged and tallied against `label` for the response."""
    try:
        return await coro
    except Exception as e:
        log.warning("%s failed: %s: %s", label or "step", type(e).__name__, e)
        tally = _failures.get()
        if tally is not None:
            tally[label or "unknown"] += 1
        return default


# Internal _safe() labels -> what the sales team should actually read.
_SOURCE_NAMES = {
    "bse_instruments": "BSE",
    "bse_suggest":     "BSE",
    "mca":             "MCA/Zauba",
    "credit_history":  "Rating history",
    "fit_analysis":    "AI fit analysis",
    "fundamentals":    "NSE financials",
    "scoring":         "Lead scoring",
    "google_places":   "Google Places",
}


def _llm_configured() -> bool:
    from backend.pipeline.llm import _providers
    return bool(_providers())


def _cra_status() -> list[dict]:
    """Per-agency scrape health, so a dark scraper is visible to uptime checks.

    Without this the four CRA sources are invisible: `_source_status` only knows
    about labels that failed inside a request, so an agency whose circuit breaker
    is open between requests looks perfectly healthy. Seven sites with no APIs
    and no contract will break, and a silently dark pipeline is the failure mode
    this product cannot afford."""
    from backend.pipeline import cra_press

    out = []
    for name, state in cra_press._breaker.items():
        tripped = cra_press._tripped(name)
        out.append({
            "name": f"CRA:{name}",
            "ok": not tripped,
            "detail": "circuit breaker open - site unreachable or blocking" if tripped else "",
        })
    for name in cra_press._STATICALLY_BLOCKED:
        out.append({
            "name": f"CRA:{name}",
            "ok": True,   # known and intended, not a fault to alert on
            "detail": "no scrape path - see CRA_ENDPOINTS.md",
        })
    return out


def _source_status(total: int) -> list[dict]:
    """What each data source did on this request - surfaced in the response so
    the UI can badge a degraded source instead of implying an empty truth."""
    from backend.pipeline.bse_scraper import _bse_tripped

    # display name -> failure detail; "" means healthy. Keying by display name
    # merges the several internal labels that mean "BSE" into one badge.
    status: dict[str, str] = {
        "RBI/NSE registry": "" if registry_store.available()
                            else "registry.sqlite missing - run the ingest CLI",
        # Which store is actually holding searches. Worth surfacing: the whole
        # point is that a restart must not start 404-ing CSV exports.
        f"Search store ({database.backend_name()})": "",
        "BSE": "circuit breaker open - BSE unreachable or blocking" if _bse_tripped() else "",
        "AI scoring": "" if _llm_configured()
                      else "no LLM key configured - rule-based scores only",
    }
    for label, n in (_failures.get() or Counter()).items():
        name = _SOURCE_NAMES.get(label, label)
        if not status.get(name):
            status[name] = (f"{n} of {total} lookups failed" if total > 1
                            else "lookup failed")
    return [{"name": n, "ok": not d, "detail": d} for n, d in status.items()]


@app.post("/api/search", response_model=SearchResponse)
async def search_leads(req: SearchRequest):
    if not req.city.strip():
        raise HTTPException(status_code=400, detail="City or pincode is required")

    _failures.set(Counter())
    entity_type = req.entity_type or "All"
    instrument_type = req.instrument_type or "All"
    industry = req.industry or f"{entity_type} - {instrument_type}"

    raw_companies, city_lat, city_lng = await discover_companies(
        req.city, industry, entity_type, instrument_type, size=req.size or "All"
    )
    log.info("search city=%r entity=%s instrument=%s -> %d candidates",
             req.city, entity_type, instrument_type, len(raw_companies))
    if not raw_companies:
        # Return empty result with city coordinates so map still zooms
        return SearchResponse(
            companies=[], city_lat=city_lat, city_lng=city_lng,
            search_id=str(uuid.uuid4()), sources=_source_status(0),
        )

    # Enrich contacts - safe, returns companies with empty contacts on failure
    try:
        raw_companies = await enrich_contacts(raw_companies)
    except Exception:
        for c in raw_companies:
            c.setdefault("contacts", [])

    # Throttle enrichment so 60 leads don't mean 120+ concurrent BSE calls
    _sem = asyncio.Semaphore(8)

    # Canary: if BSE is down/blocking, trip the breaker once up front
    await bse_health_check()

    async def _enrich_one(c: dict) -> dict:
        async with _sem:
            sub = c.get("registry_sub_type", "")
            is_coop = sub in ("Co-operative Bank", "Scheduled UCB")

            # MCA data - skipped when we already know the CIN (registry NBFCs:
            # incorporation year is encoded in the CIN itself) and for co-op
            # banks (cooperative societies, not MCA companies at all).
            # Directors load on click via Company Research.
            if c.get("cin"):
                year = c["cin"][8:12]
                c["incorporation_date"] = year if year.isdigit() else ""
                c.setdefault("directors", [])
            elif not is_coop:
                mca = await _safe(fetch_mca_data(c["name"], skip_zauba=True), {}, "mca")
                c["cin"] = mca.get("cin", "")
                c["incorporation_date"] = mca.get("incorporation_date", "")
                c["directors"] = mca.get("directors", [])
                if not c.get("address") and mca.get("registered_address"):
                    c["address"] = mca["registered_address"]
            else:
                c.setdefault("incorporation_date", "")
                c.setdefault("directors", [])

            # Registry email (RBI-filed contact) becomes the first contact
            if c.get("registry_email"):
                contacts = c.get("contacts") or []
                if not any(ct.get("email") == c["registry_email"] for ct in contacts):
                    contacts.insert(0, {
                        "name": "Registered contact",
                        "email": c["registry_email"],
                        "position": "RBI-filed email",
                        "linkedin_url": "",
                    })
                c["contacts"] = contacts

            # BSE instruments - non-scheduled co-op banks never list debt on
            # BSE; skip 4 wasted searches each
            if sub == "Co-operative Bank":
                c["past_instruments"] = []
            else:
                c["past_instruments"] = await _safe(
                    fetch_past_instruments(c["name"]), [], "bse_instruments")

            # Scoring: rule-based only in bulk search (fast, deterministic).
            # The LLM still powers fit analysis in Company Research.
            c = await _safe(
                score_company(c, industry, req.city, c.get("entity_type", entity_type),
                              instrument_type, use_llm=False),
                c, "scoring",
            )
            c.setdefault("score", 0)
            c.setdefault("score_label", "Pending")
            c.setdefault("why_quality_lead", [])
            c.setdefault("pain_points", [])
            c.setdefault("recommended_approach", "")

            # Office locations: fetched on demand via /api/offices when a lead
            # is selected - not in bulk (60 Google calls per search)
            c["office_locations"] = []

            return c

    enriched = await asyncio.gather(*[_enrich_one(c) for c in raw_companies])

    companies: list[Company] = []
    for c in enriched:
        directors = [Director(**{k: str(v) for k, v in d.items() if k in Director.model_fields})
                     for d in c.get("directors", [])]
        contacts = [Contact(**{k: str(v or "") for k, v in ct.items() if k in Contact.model_fields})
                    for ct in c.get("contacts", [])]
        past_instruments = [
            PastInstrument(**{k: str(v or "") for k, v in inst.items() if k in PastInstrument.model_fields})
            for inst in c.get("past_instruments", [])
        ]
        office_locations = [
            OfficeLocation(**{k: v for k, v in loc.items() if k in OfficeLocation.model_fields})
            for loc in c.get("office_locations", [])
        ]
        companies.append(Company(
            id=c["id"],
            name=c["name"],
            address=c.get("address", ""),
            lat=c["lat"],
            lng=c["lng"],
            website=c.get("website", ""),
            phone=c.get("phone", ""),
            cin=c.get("cin", ""),
            incorporation_date=c.get("incorporation_date", ""),
            entity_type=c.get("entity_type", entity_type),
            sub_type=c.get("registry_sub_type", ""),
            layer=c.get("registry_layer", ""),
            discovery_source=c.get("discovery_source", ""),
            directors=directors,
            contacts=contacts,
            past_instruments=past_instruments,
            office_locations=office_locations,
            score=c.get("score", 0),
            score_label=c.get("score_label", "Pending"),
            why_quality_lead=c.get("why_quality_lead", []),
            pain_points=c.get("pain_points", []),
            recommended_approach=c.get("recommended_approach", ""),
        ))

    companies.sort(key=lambda x: x.score, reverse=True)

    search_id = str(uuid.uuid4())
    _search_cache[search_id] = [c.model_dump() for c in companies]
    database.save_search(search_id, req.city, industry, companies)

    sources = _source_status(len(companies))
    degraded = [s["name"] for s in sources if not s["ok"]]
    if degraded:
        log.warning("search %s degraded sources: %s", search_id[:8], ", ".join(degraded))

    return SearchResponse(
        companies=companies,
        city_lat=city_lat,
        city_lng=city_lng,
        search_id=search_id,
        sources=sources,
    )


# ── Ranked lead queue (ROADMAP_V3 lever 3) ───────────────────────────────────

@app.get("/api/queue")
async def get_queue(days: int = 30, enrich: int = 15):
    """The winnability-ranked queue the dashboard renders.

    Degrades rather than 500s: if every CRA source is unreachable the queue comes
    back empty with `coverage` saying so, which is a different and honest answer
    from "no leads". The caller must render the difference."""
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")
    # Each enriched lead costs two CARE calls, so the depth is capped rather
    # than left to the caller - an unbounded value would let one request
    # hammer CARE and stall the page.
    enrich = max(0, min(enrich, 40))

    _failures.set(Counter())
    empty = {"leads": [], "total": 0, "workable": 0, "blocked": 0,
             "window_days": days,
             "coverage": {"sources": {}, "agencies_read": [], "agencies_total": 0,
                          "data_status": "unverified",
                          "note": "Queue could not be built - no CRA source answered."}}
    data = await _safe(build_queue(days=days, enrich=enrich), empty, "cra_press")
    return {**data, "sources": _source_status(1)}


# ── ACER's own book: renewals, and never pitching our own clients ────────────

@app.get("/api/renewals")
async def get_renewals(days: int = 90):
    """ACER clients whose annual surveillance falls due inside the window.

    The cheapest revenue in the business and nobody was tracking it. Also
    reports how stale the underlying file is: acerratings.com 403s plain HTTP,
    so the book is hand-maintained and a forgotten file would quietly stop
    recognising new clients."""
    if days < 1 or days > 730:
        raise HTTPException(status_code=400, detail="days must be between 1 and 730")
    return {
        "renewals": acer_book.renewals(within_days=days),
        "window_days": days,
        "book": acer_book.staleness(),
    }


# ── Fundamentals: size the opportunity ───────────────────────────────────────

@app.get("/api/fundamentals/{symbol}")
async def get_fundamentals(symbol: str):
    """Filed financials for one NSE symbol, with interest coverage.

    From NSE's own Ind-AS XBRL rather than a data vendor: both free commercial
    tiers were tested with live keys and neither covers India (see the
    fundamentals module docstring)."""
    return await _safe(fetch_fundamentals(symbol),
                       {"status": "unverified", "symbol": symbol}, "fundamentals")


# ── Refinance window: bonds maturing soon ────────────────────────────────────

@app.get("/api/refinance")
async def get_refinance(months: int = 9):
    """Issuers with debt maturing inside the window, from the BSE scrip master
    we already cache.

    The one genuinely FORWARD-looking signal in the tool: the CRA feeds only ever
    say what already happened, whereas a bond maturing has to be refinanced and
    refinancing needs a rating. Note `maturity_precision` on each row - BSE's
    scrip-id convention usually yields only a year, so most rows are
    year-accurate, not day-accurate."""
    if months < 1 or months > 36:
        raise HTTPException(status_code=400, detail="months must be between 1 and 36")

    _failures.set(Counter())
    empty = {"candidates": [], "window_months": months, "data_status": "unverified"}
    data = await _safe(find_refinance_candidates(months_ahead=months), empty, "bse_instruments")
    return {**data, "sources": _source_status(1)}


# ── My Pipeline: saved leads + outcome log (ROADMAP_V3 phase 3) ──────────────
# No accounts yet, so this is one shared list for the whole BD team. Per-user
# separation needs auth, and no Supabase project exists yet.

class SaveLeadRequest(BaseModel):
    company_name: str
    # Identity, when the finding source had one. The CRA feeds do not, so the
    # store falls back to the folded name - see pipeline_store.norm_name.
    cin: str | None = None
    winnability: int | None = None
    flags: dict | None = None
    agencies_seen: list[str] | None = None


class StageRequest(BaseModel):
    stage: str
    note: str = ""


@app.get("/api/leads")
async def get_saved_leads(stage: str | None = None):
    return {"leads": pipeline_store.list_leads(stage),
            "funnel": pipeline_store.funnel(),
            "stages": pipeline_store.STAGES}


@app.get("/api/leads/events")
async def get_lead_events(company_name: str | None = None, limit: int = 200):
    """The outcome log. This is what eventually lets the winnability weights be
    fitted to what actually converts, instead of staying flat guesses.

    Declared before /api/leads/{company_name} routes so "events" is not captured
    as a company name."""
    return {"events": pipeline_store.events(company_name, min(max(limit, 1), 1000))}


@app.post("/api/leads")
async def add_saved_lead(req: SaveLeadRequest):
    try:
        return pipeline_store.save_lead(req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/leads/{company_name}/stage")
async def move_saved_lead(company_name: str, req: StageRequest):
    try:
        return pipeline_store.set_stage(company_name, req.stage, req.note)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"{company_name} is not saved")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/leads/{company_name}")
async def delete_saved_lead(company_name: str):
    if not pipeline_store.remove_lead(company_name):
        raise HTTPException(status_code=404, detail=f"{company_name} is not saved")
    return {"removed": company_name}


# ── Briefing: news for names already in the pipeline ─────────────────────────


@app.get("/api/briefing")
async def get_briefing(limit_per_lead: int = 8):
    """What the papers said about the companies we are about to call.

    Not a market feed: it only speaks about saved leads. An unreachable set of
    publishers must read as unreachable, never as "no news", so `status` and
    `sources_fail` ride along and the UI has to render them."""
    leads = pipeline_store.list_leads()
    if not leads:
        return {"briefs": [], "with_news": 0, "scanned": 0, "status": "empty",
                "sources_ok": [], "sources_fail": [],
                "note": "No saved leads, so there is nothing to brief."}
    empty = {"briefs": [], "with_news": 0, "scanned": 0, "status": "blocked",
             "sources_ok": [], "sources_fail": ["news feeds unreachable"]}
    return await _safe(build_briefing(leads, max(1, min(limit_per_lead, 25))),
                       empty, "rss_news")


# ── Pitch brief: one printable page per company ──────────────────────────────


async def _briefs_for(leads: list[dict]) -> list[dict]:
    """Assemble what each sheet prints: the stored lead, its history, its news.

    News is best-effort on purpose - a dead RSS feed must not cost someone the
    brief they are printing on the way to a meeting."""
    news_by_company: dict[str, list[dict]] = {}
    data = await _safe(build_briefing(leads), {"briefs": []}, "rss_news")
    for b in data.get("briefs", []):
        news_by_company[b["company_name"]] = b.get("items", [])
    return [{"lead": lead,
             "events": pipeline_store.events(lead["company_name"], limit=12),
             "news": news_by_company.get(lead["company_name"], [])}
            for lead in leads]


@app.get("/api/brief", response_class=HTMLResponse)
async def brief_pack(stage: str | None = None):
    """The whole pipeline as a print-ready pack, one A4 sheet per company.

    HTML, not a PDF binary: the browser's print-to-PDF makes the file, which
    keeps a PDF engine out of the dependency list entirely."""
    leads = pipeline_store.list_leads(stage)
    return pitch_brief.render(await _briefs_for(leads),
                              title=f"ACER-IQ pitch briefs{f' - {stage}' if stage else ''}")


@app.get("/api/brief/{company_name}", response_class=HTMLResponse)
async def brief_one(company_name: str):
    leads = [l for l in pipeline_store.list_leads()
             if pipeline_store.norm_name(l["company_name"]) == pipeline_store.norm_name(company_name)]
    if not leads:
        raise HTTPException(status_code=404, detail=f"{company_name} is not saved")
    return pitch_brief.render(await _briefs_for(leads),
                              title=f"ACER-IQ pitch brief - {leads[0]['company_name']}")


# ── Sector Indices (Signal Radar) ────────────────────────────────────────────

@app.get("/api/sectors")
async def get_sectors():
    return await fetch_sector_indices()


# ── Market News ──────────────────────────────────────────────────────────────

@app.get("/api/news")
async def get_market_news(days: int = 7, source: str = "all"):
    if days < 1 or days > 30:
        raise HTTPException(status_code=400, detail="days must be between 1 and 30")

    if source == "nse":
        return await fetch_market_news(days)
    if source == "rss":
        return await fetch_rss_news()

    import asyncio
    nse_task = asyncio.create_task(fetch_market_news(days))
    rss_task = asyncio.create_task(fetch_rss_news())
    nse_data, rss_data = await asyncio.gather(nse_task, rss_task)

    nse_items = nse_data.get("items", [])
    for it in nse_items:
        it.setdefault("source", "NSE")
        it.setdefault("link", "")
        it.setdefault("description", "")

    rss_all = rss_data.get("all_items", [])

    combined = nse_items + rss_all
    combined.sort(key=lambda x: x.get("date", ""), reverse=True)

    sources_summary = ["NSE"]
    if nse_data.get("status") == "blocked":
        sources_summary[0] = "NSE (unreachable)"
    sources_summary.extend(rss_data.get("sources_ok", []))
    sources_summary.extend(
        f"{s} (failed)" for s in rss_data.get("sources_fail", [])
    )

    return {
        "items": combined[:300],
        "status": "ok" if combined else "empty",
        "sources": sources_summary,
        "nse_raw": nse_data.get("total_raw", 0),
        "nse_signals": nse_data.get("total_filtered", 0),
        "rss_total": rss_data.get("total_items", 0),
        "rss_signals": rss_data.get("total_signals", 0),
        "from_date": nse_data.get("from_date", ""),
        "to_date": nse_data.get("to_date", ""),
        "total_items": len(combined),
    }


# ── Company Autocomplete ─────────────────────────────────────────────────────

@app.get("/api/company-suggest")
async def company_suggest(q: str = ""):
    """Company suggestions: RBI registry first (10,500+ entities, instant,
    includes CIN), BSE-listed companies appended when BSE is reachable."""
    if len(q.strip()) < 2:
        return {"suggestions": []}

    suggestions = registry_store.suggest(q.strip(), limit=8)
    seen = {s["name"].lower() for s in suggestions}

    # Supplement with BSE-listed companies (covers corporates outside RBI lists).
    # Served from the cached BSE scrip master - no per-keystroke network call.
    from backend.pipeline.bse_scraper import search_bse_companies
    for item in await _safe(search_bse_companies(q.strip(), limit=10), [], "bse_suggest"):
        if item["name"].lower() in seen:
            continue
        suggestions.append({
            "name": item["name"], "cin": "", "bse_code": item["bse_code"],
            "sector": item["sector"] or "BSE-listed", "source": "bse",
        })
        seen.add(item["name"].lower())

    return {"suggestions": suggestions[:15]}


# ── Company Credit History ────────────────────────────────────────────────────

class CompanyCreditRequest(BaseModel):
    query: str  # company name or CIN


@app.post("/api/company-credit")
async def company_credit(req: CompanyCreditRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Company name or CIN is required")

    _failures.set(Counter())
    query = req.query.strip()

    # Detect CIN pattern (starts with L or U followed by digits and letters)
    is_cin = len(query) == 21 and query[0].upper() in ("L", "U")

    # 1) RBI registry - authoritative for NBFCs / co-op banks / SFBs / ARCs
    reg = registry_store.get_by_name(query)

    # Every downstream lookup (BSE, Zauba, NSE) is a *name* search. Feeding it a
    # CIN returns nothing, which the UI would render as "not rated by anyone" -
    # a false negative on the one screen the sales team trusts. Fail loudly.
    if is_cin and not reg:
        raise HTTPException(
            status_code=404,
            detail=f"CIN {query} is not in the registry. Search by company name "
                   f"instead - CIN-to-name resolution needs MCA data, which we "
                   f"do not have a free source for yet.",
        )

    company_info: dict = {
        "name": (reg or {}).get("name") or query,
        "cin": (reg or {}).get("cin") or (query if is_cin else ""),
        "address": (reg or {}).get("address", ""),
        "email": (reg or {}).get("email", ""),
        "entity_type": (reg or {}).get("entity_type") or _guess_entity_type(query),
        "sub_type": (reg or {}).get("sub_type", ""),
        "layer": (reg or {}).get("layer", ""),
        "deposit_taking": (reg or {}).get("deposit_taking", False),
        "data_source": (reg or {}).get("source", ""),
        "incorporation_date": "",
        "directors": [],
    }
    if company_info["cin"] and len(company_info["cin"]) == 21:
        year = company_info["cin"][8:12]
        if year.isdigit():
            company_info["incorporation_date"] = year

    # 2) BSE/Zauba - listing data, directors, registered address
    mca = await _safe(fetch_mca_data(company_info["name"]), {}, "mca")
    if mca.get("cin") and not company_info["cin"]:
        company_info["cin"] = mca["cin"]
    if mca.get("registered_address") and not company_info["address"]:
        company_info["address"] = mca["registered_address"]
    if mca.get("directors"):
        company_info["directors"] = mca["directors"]
    if mca.get("incorporation_date") and not company_info["incorporation_date"]:
        company_info["incorporation_date"] = mca["incorporation_date"]
    if not company_info["address"]:
        company_info["address"] = "India"

    # Fetch credit history (NSE disclosures + announcements + BSE)
    company_info["symbol"] = (reg or {}).get("symbol", "")
    search_name = company_info["name"] if company_info["name"] != query else query
    credit_data = await _safe(fetch_credit_history(search_name, symbol=company_info["symbol"]), {
        "agencies": [],
        "total_instruments": 0,
        "rated_by_count": 0,
        "raw_instruments": [],
    }, "credit_history")

    # AI fit analysis
    fit = await _safe(analyze_fit(company_info, credit_data), {
        "fit_score": 0,
        "fit_label": "Pending",
        "opportunity_type": "Analysis unavailable",
        "key_insights": [],
        "watch_outs": [],
        "recommended_action": "Set OPENROUTER_API_KEY for AI fit analysis",
        "best_instrument_pitch": "NCD",
        "urgency": "Medium",
        "already_rated_by_infomerics": False,
    }, "fit_analysis")

    # Winnability - can we realistically win this, not merely does it need a
    # rating (ROADMAP_V3 lever 3). Pure function over credit_data, no I/O, so it
    # cannot fail the request; it stays outside _safe deliberately.
    win = winnability_scorer.score(company_info, credit_data)

    # Filed financials, when the company is NSE-listed and has filed. Sizing was
    # the gap: the fit score knows entity type and instrument count but nothing
    # about how much debt is actually being serviced.
    fundamentals = await _safe(
        fetch_fundamentals(company_info.get("symbol", "")),
        {"status": "not_filed"}, "fundamentals",
    ) if company_info.get("symbol") else {"status": "not_listed"}

    return {
        "company": company_info,
        "credit_data": credit_data,
        "fit_analysis": fit,
        "winnability": win,
        "fundamentals": fundamentals,
        "sources": _source_status(1),
    }


def _guess_entity_type(name: str) -> str:
    n = name.upper()
    if any(w in n for w in ["BANK", "BANKING"]):
        return "Bank"
    if any(w in n for w in ["NBFC", "FINANCE", "FINSERV", "CAPITAL", "LEASING", "HOUSING"]):
        return "NBFC"
    return "Corporate"


# ── Directors on demand (Find Leads drill-down) ──────────────────────────────

@app.get("/api/directors/{company_name}")
async def get_directors(company_name: str):
    """Board of directors for a selected lead - BSE CorpInfo for listed
    companies, Zauba for private ones. Cached + circuit-breaker protected."""
    mca = await _safe(fetch_mca_data(company_name), {}, "mca")
    return {
        "directors": mca.get("directors", []),
        "cin": mca.get("cin", ""),
        "incorporation_date": mca.get("incorporation_date", ""),
    }


# ── Offices & Instruments on demand ──────────────────────────────────────────

@app.get("/api/offices/{company_name}")
async def get_offices(company_name: str, lat: float = 20.5937, lng: float = 78.9629):
    offices = await _safe(find_office_locations(company_name, lat, lng), [], "google_places")
    return {"offices": offices}


@app.get("/api/instruments/{company_name}")
async def get_instruments(company_name: str):
    instruments = await _safe(fetch_past_instruments(company_name), [], "bse_instruments")
    return {"instruments": instruments}


# ── CSV Export ────────────────────────────────────────────────────────────────

@app.get("/api/export/{search_id}")
async def export_csv(search_id: str):
    cached = _search_cache.get(search_id)
    if not cached:
        try:
            db_row = database.load_search(search_id)
            if db_row:
                cached = json.loads(db_row["results"])
        except Exception as e:
            log.error("export fallback for %s failed: %s: %s",
                      search_id, type(e).__name__, e)
    if not cached:
        raise HTTPException(status_code=404, detail="Search not found")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Name", "Entity Type", "Score", "Score Label", "Address",
        "Website", "Phone", "CIN", "Incorporated",
        "Past Instruments Count", "Directors", "Contacts",
        "Why Quality Lead", "Pain Points", "Recommended Approach",
    ])
    for c in cached:
        writer.writerow([
            c.get("name"), c.get("entity_type"), c.get("score"), c.get("score_label"),
            c.get("address"), c.get("website"), c.get("phone"), c.get("cin"),
            c.get("incorporation_date"), len(c.get("past_instruments", [])),
            " | ".join(d.get("name", "") for d in c.get("directors", [])),
            " | ".join(f"{ct.get('name')} <{ct.get('email')}>" for ct in c.get("contacts", [])),
            " • ".join(c.get("why_quality_lead", [])),
            " • ".join(c.get("pain_points", [])),
            c.get("recommended_approach"),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=leads_{search_id[:8]}.csv"},
    )


# ── Serve the app ────────────────────────────────────────────────────────────
# Opening this service in a browser must render ACER-IQ. Two ways that happens:
#
#   1. A build sits next to us (local dev after `npm run build`, or any host
#      that builds both halves) -> serve it directly, same origin, no CORS.
#   2. There is no build here (the Render backend only installs Python deps)
#      -> send the browser to wherever the UI is deployed.
#
# Without case 2 the API host answers "/" with {"detail":"Not Found"} and the
# only human-readable page is /docs, so anyone opening the backend URL gets
# Swagger instead of the product.


@app.get("/api/health")
async def health():
    """Liveness for uptime checks - kept off "/" so the root can serve the UI."""
    _failures.set(Counter())
    sources = _source_status(0) + _cra_status()
    # No LLM key is a supported mode, not a fault - PREMORTEM.md #6: every
    # LLM-produced field has a rule-based fallback, so this never blanks a
    # row. Still reported in `sources` (the per-search banner needs it) but
    # not paged on here, same treatment as the statically-blocked CRA sites.
    degraded = [s["name"] for s in sources if not s["ok"] and s["name"] != "AI scoring"]
    if degraded:
        # Logged at WARNING so an always-on host's log alerting can fire on it
        # without anything having to poll this endpoint.
        log.warning("health: degraded sources %s", ", ".join(degraded))
    return {
        # "ok" still means the process is alive; "degraded" is the signal an
        # uptime check should page on, and it must not be buried in a sub-list.
        "status": "degraded" if degraded else "ok",
        "version": app.version,
        "ui": "bundled" if _FRONTEND_DIST.exists() else settings.frontend_url,
        "degraded": degraded,
        "sources": sources,
    }


_FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="static")
else:
    log.info("no frontend/dist here - redirecting browsers to %s",
             settings.frontend_url)

    @app.get("/", include_in_schema=False)
    async def root_to_app():
        return RedirectResponse(settings.frontend_url)

    @app.get("/{path:path}", include_in_schema=False)
    async def any_page_to_app(path: str):
        # Only browser routes land here; /api/* and /docs are matched earlier.
        return RedirectResponse(f"{settings.frontend_url}/{path}")
