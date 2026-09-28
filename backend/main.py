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
from fastapi import FastAPI, Header, HTTPException
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
from backend.pipeline import action_history, news_archive, source_health
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
    # DELETE is how a lead is removed from the pipeline; without it the
    # browser's preflight fails cross-origin and Remove silently never works.
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

_search_cache: dict[str, list[dict]] = {}


@app.on_event("startup")
async def _load_source_history() -> None:
    """Merge the durable per-source history before the first fetch records
    over it - in a thread, since it is a blocking Supabase read."""
    await asyncio.to_thread(source_health.ensure_loaded)

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


def _ai_status() -> str:
    """"" when AI is working; otherwise why not. Configured is not the same as
    working: a retired free model left every answer rule-based for days
    while this said "ok" (found 2026-09-28)."""
    if not _llm_configured():
        return "no LLM key configured - rule-based scores only"
    bad = [s for s in source_health.snapshot("AI (") if s["state"] in ("failing", "down")]
    if bad:
        return f"configured, but {bad[0]['message']} - rule-based until it recovers"
    return ""


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
        "AI scoring": _ai_status(),
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
    # Per-agency last good read, so an empty queue says whether it is a quiet
    # window or a dead feed (PREMORTEM.md section 2).
    fresh = source_health.snapshot("CRA:")
    return {**data, "sources": _source_status(1),
            "freshness": fresh, "empty_means": source_health.summarise(
                [f for f in fresh if f["source"] in _CRA_FEEDS])}


# The agencies whose feeds build the queue; CARE is lookup-only enrichment.
_CRA_FEEDS = ("CRA:ACUITE", "CRA:BRICKWORK", "CRA:INDRA")


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
# Per-user when Supabase is configured: the caller's Supabase JWT rides in the
# Authorization header and row level security scopes every row to them
# (pipeline_store's per-user section). Without Supabase (local dev) it is the
# one shared SQLite list, as before.


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


async def _pipeline(fn):
    """Run a pipeline_store call, mapping its outcomes to status codes the UI
    renders differently: sign in (401), store down (503), moved meanwhile (409).

    In a worker thread: the per-user path is synchronous HTTP to Supabase, and
    run inline it would stall every other request on the event loop."""
    try:
        return await asyncio.to_thread(fn)
    except pipeline_store.AuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except pipeline_store.StoreUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except pipeline_store.Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except pipeline_store.Forbidden as e:
        raise HTTPException(status_code=403, detail=str(e))


class SaveLeadRequest(BaseModel):
    company_name: str
    # Identity, when the finding source had one. The CRA feeds do not, so the
    # store falls back to the folded name - see pipeline_store.norm_name.
    cin: str | None = None
    winnability: int | None = None
    flags: dict | None = None
    agencies_seen: list[str] | None = None
    # BD profile the lead belongs to (bd_roster.json id); "" for Admin.
    owner: str | None = None
    # "list" (from the monthly BD list / queue) or "self_sourced" (the BD's
    # own lead - needs a CIN and a one-sentence reason, BD_LIST_SPEC.md 3).
    origin: str | None = None
    reason: str | None = None


class StageRequest(BaseModel):
    stage: str
    note: str = ""
    # The stage's mandatory fields (pipeline_store.STAGE_FIELDS).
    details: dict | None = None


class StatusRequest(BaseModel):
    # Pending | In progress | Won | Lost  ("Closed" in the UI forces Won/Lost)
    status: str
    note: str = ""
    lost_reason: str | None = None


class ReassignRequest(BaseModel):
    owner: str
    reason: str


@app.get("/api/leads")
async def get_saved_leads(stage: str | None = None, scope: str = "mine",
                          owner: str = "", authorization: str | None = Header(None)):
    """`scope=all` is the team view (Admin profile, or one BD's leads with
    `owner`); `scope=mine` is the signed-in user's own rows."""
    tok = _bearer(authorization)
    if scope == "all":
        leads = await _pipeline(lambda: pipeline_store.list_all_leads(tok, owner))
        if stage:
            leads = [l for l in leads if l["stage"] == stage]
        counts = {s: 0 for s in pipeline_store.STAGES}
        for l in leads:
            counts[l["stage"]] = counts.get(l["stage"], 0) + 1
        return {"leads": leads, "funnel": counts, "stages": pipeline_store.STAGES,
                "per_user": pipeline_store.per_user(), "scope": "all"}
    leads = await _pipeline(lambda: pipeline_store.list_leads(stage, token=tok))
    counts = await _pipeline(lambda: pipeline_store.funnel(token=tok))
    return {"leads": leads, "funnel": counts, "stages": pipeline_store.STAGES,
            "per_user": pipeline_store.per_user()}


@app.get("/api/leads/events")
async def get_lead_events(company_name: str | None = None, limit: int = 200,
                          authorization: str | None = Header(None)):
    """The outcome log. This is what eventually lets the winnability weights be
    fitted to what actually converts, instead of staying flat guesses.

    Declared before /api/leads/{company_name} routes so "events" is not captured
    as a company name."""
    tok = _bearer(authorization)
    return {"events": await _pipeline(lambda: pipeline_store.events(
        company_name, min(max(limit, 1), 1000), token=tok))}


@app.post("/api/leads")
async def add_saved_lead(req: SaveLeadRequest,
                         authorization: str | None = Header(None)):
    tok = _bearer(authorization)
    lead = req.model_dump()
    try:
        if lead.get("origin") == "self_sourced":
            pipeline_store.validate_self_sourced(lead)
        return await _pipeline(lambda: pipeline_store.save_lead(lead, token=tok))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/leads/{company_name}/stage")
async def move_saved_lead(company_name: str, req: StageRequest,
                          authorization: str | None = Header(None)):
    tok = _bearer(authorization)
    try:
        return await _pipeline(lambda: pipeline_store.set_stage(
            company_name, req.stage, req.note, token=tok, details=req.details))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"{company_name} is not saved")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/leads/{company_name}/status")
async def set_lead_status(company_name: str, req: StatusRequest,
                          authorization: str | None = Header(None)):
    """The BD's one click (operator: statuses, not stages). Who changed it and
    when is stamped by the server - the BD types nothing (Head of BD)."""
    stage = pipeline_store.SIMPLE_STATUS.get(req.status)
    if stage is None:
        raise HTTPException(status_code=400, detail="status must be Pending, In progress, Won or Lost")
    tok = _bearer(authorization)
    who = await _pipeline(lambda: pipeline_store.caller(tok))
    details = {"note": req.note.strip()[:300], "changed_by": who.get("email") or "local"}
    if stage == "Lost" and req.lost_reason:
        details["lost_reason"] = req.lost_reason
    try:
        return await _pipeline(lambda: pipeline_store.set_stage(
            company_name, stage, req.note, token=tok, details=details))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"{company_name} is not saved")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/leads/{company_name}/reassign")
async def reassign_lead(company_name: str, req: ReassignRequest,
                        authorization: str | None = Header(None)):
    """Admin only (BD_LIST_SPEC.md 4): move a lead to another BD with a reason."""
    from backend.pipeline import bd_list
    tok = _bearer(authorization)
    owners = {b["id"] for b in bd_list.roster()}
    try:
        return await _pipeline(lambda: pipeline_store.reassign(
            company_name, req.owner, req.reason, tok, owners))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"{company_name} is not saved")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/pipeline/schema")
async def pipeline_schema():
    """What each stage move must record - the single source of truth the
    stage-move form is built from, so the UI and the validator never drift."""
    return {"stages": pipeline_store.STAGES,
            "fields": {st: [{"name": f, "kind": k, "options": o,
                             "required": r and pipeline_store.MANDATORY_FIELDS}
                            for f, (k, o, r) in spec.items()]
                       for st, spec in pipeline_store.STAGE_FIELDS.items()},
            "instruments": pipeline_store.INSTRUMENTS,
            "statuses": ["Pending", "In progress", "Closed"],
            "closed_results": ["Won", "Lost"],
            "lost_reasons": pipeline_store.SIMPLE_LOST_REASONS,
            "stale_days": pipeline_store.STALE_DAYS}


@app.get("/api/pipeline/team")
async def pipeline_team(authorization: str | None = Header(None)):
    """The Admin screen: per BD coverage, funnel, conversion, Rs cr and fees,
    overdue follow-ups, lost reasons, self-sourced count, and clashes."""
    from backend.pipeline import bd_list
    tok = _bearer(authorization)
    await _pipeline(lambda: pipeline_store.require_admin(tok))
    leads = await _pipeline(lambda: pipeline_store.list_all_leads(tok))
    snap = await asyncio.to_thread(bd_list.load, bd_list.month_key())
    summary = pipeline_store.team_summary(leads, (snap or {}).get("rows", []), bd_list.roster())
    return summary | {"month": bd_list.month_key(), "list_generated": bool(snap),
                      "stale_inputs": [k for k, v in ((snap or {}).get("inputs") or {}).items()
                                       if not k.startswith("_") and v.get("status") != "ok"]}


@app.delete("/api/leads/{company_name}")
async def delete_saved_lead(company_name: str,
                            authorization: str | None = Header(None)):
    tok = _bearer(authorization)
    if not await _pipeline(lambda: pipeline_store.remove_lead(company_name, token=tok)):
        raise HTTPException(status_code=404, detail=f"{company_name} is not saved")
    return {"removed": company_name}


# ── Briefing: news for names already in the pipeline ─────────────────────────


@app.get("/api/briefing")
async def get_briefing(limit_per_lead: int = 8,
                       authorization: str | None = Header(None)):
    """What the papers said about the companies we are about to call.

    Not a market feed: it only speaks about saved leads. An unreachable set of
    publishers must read as unreachable, never as "no news", so `status` and
    `sources_fail` ride along and the UI has to render them."""
    tok = _bearer(authorization)
    leads = await _pipeline(lambda: pipeline_store.list_leads(token=tok))
    if not leads:
        return {"briefs": [], "with_news": 0, "scanned": 0, "status": "empty",
                "sources_ok": [], "sources_fail": [],
                "note": "No saved leads, so there is nothing to brief."}
    empty = {"briefs": [], "with_news": 0, "scanned": 0, "status": "blocked",
             "sources_ok": [], "sources_fail": ["news feeds unreachable"]}
    return await _safe(build_briefing(leads, max(1, min(limit_per_lead, 25))),
                       empty, "rss_news")


# ── Pitch brief: one printable page per company ──────────────────────────────


async def _briefs_for(leads: list[dict], tok: str | None = None) -> list[dict]:
    """Assemble what each sheet prints: the stored lead, its history, its news.

    News is best-effort on purpose - a dead RSS feed must not cost someone the
    brief they are printing on the way to a meeting."""
    news_by_company: dict[str, list[dict]] = {}
    data = await _safe(build_briefing(leads), {"briefs": []}, "rss_news")
    for b in data.get("briefs", []):
        news_by_company[b["company_name"]] = b.get("items", [])
    out = []
    for lead in leads:
        name = lead["company_name"]
        history = await _pipeline(
            lambda: pipeline_store.events(name, limit=12, token=tok))
        out.append({"lead": lead, "events": history,
                    "news": news_by_company.get(name, [])})
    return out


@app.get("/api/brief", response_class=HTMLResponse)
async def brief_pack(stage: str | None = None,
                     authorization: str | None = Header(None)):
    """The whole pipeline as a print-ready pack, one A4 sheet per company.

    HTML, not a PDF binary: the browser's print-to-PDF makes the file, which
    keeps a PDF engine out of the dependency list entirely."""
    tok = _bearer(authorization)
    leads = await _pipeline(lambda: pipeline_store.list_leads(stage, token=tok))
    return pitch_brief.render(await _briefs_for(leads, tok),
                              title=f"ACER-IQ pitch briefs{f' - {stage}' if stage else ''}")


@app.get("/api/brief/{company_name}", response_class=HTMLResponse)
async def brief_one(company_name: str, authorization: str | None = Header(None)):
    tok = _bearer(authorization)
    leads = [l for l in await _pipeline(lambda: pipeline_store.list_leads(token=tok))
             if pipeline_store.norm_name(l["company_name"]) == pipeline_store.norm_name(company_name)]
    if not leads:
        raise HTTPException(status_code=404, detail=f"{company_name} is not saved")
    return pitch_brief.render(await _briefs_for(leads, tok),
                              title=f"ACER-IQ pitch brief - {leads[0]['company_name']}")


# ── Sector Indices (Signal Radar) ────────────────────────────────────────────

@app.get("/api/sectors")
async def get_sectors():
    return await fetch_sector_indices()


# ── Market News (Tab 3: news, kept) ─────────────────────────────────────────
# Served from the archive, not the live call (BUILD_PLAN Phase 2). A live poll
# still runs when the archive is more than ten minutes behind, and folds into
# the archive - so the page shows history, and a dead feed shows as a dead
# feed rather than an emptier-looking day.

_NEWS_REFRESH_EVERY = 600
_news_polled = {"at": 0.0}
_NEWS_SOURCES = ("NSE announcements",)   # plus every RSS feed, added below


async def _refresh_news(force: bool = False) -> bool:
    import time as _time
    age = _time.time() - _news_polled["at"]
    # Even a forced refresh waits a minute: the Refresh button must not be a
    # way to hammer NSE.
    if age < (60 if force else _NEWS_REFRESH_EVERY):
        return False
    _news_polled["at"] = _time.time()
    await asyncio.gather(_safe(fetch_market_news(7), {}, "nse_news"),
                         _safe(fetch_rss_news(), {}, "rss_news"))
    return True


def _news_link(it: dict) -> str:
    if database.safe_url(it.get("link", "")):
        return database.safe_url(it["link"])
    if it.get("symbol"):
        return f"https://www.nseindia.com/get-quotes/equity?symbol={it['symbol']}"
    return ""


@app.get("/api/news")
async def get_market_news(days: int = 7, source: str = "all", major: bool = True,
                          refresh: bool = False):
    """Archived news for the window, classified. `major=true` (the default)
    keeps only items with a credit consequence (news_classify.py)."""
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")
    from backend.pipeline import news_classify
    from backend.pipeline.rss_news import FEED_NAMES

    await _refresh_news(force=refresh)
    rows = await asyncio.to_thread(news_archive.since, days)

    items = []
    for r in rows:
        if source not in ("all", "") and r["source"] != source:
            continue
        c = news_classify.classify(r)
        if major and not c["major"]:
            continue
        items.append({
            "company": r["company"], "symbol": r["symbol"],
            "subject": r["subject"], "description": r["description"],
            "source": r["source"], "categories": r["categories"],
            "published": r["date"], "date": r["date"] or r["first_seen"][:10],
            # The read date: when ACER-IQ first saw it (BUILD_PLAN invariant 2).
            "read_at": r["first_seen"],
            "link": _news_link(r),
            **c,
        })

    feeds = list(_NEWS_SOURCES) + list(FEED_NAMES)
    fresh = [source_health.status(f) for f in feeds]
    return {
        "items": items[:500],
        "total_items": len(items),
        "truncated": len(items) > 500,
        "window_days": days,
        "major_only": major,
        "freshness": fresh,
        "empty_means": source_health.summarise(fresh),
        "archive": await asyncio.to_thread(news_archive.stats),
        "sources": sorted({r["source"] for r in rows}),
        "status": "ok" if items else "empty",
    }


# ── Monthly BD list (Tab 2) ──────────────────────────────────────────────────

@app.get("/api/roster")
async def get_roster():
    """The four BD profiles (backend/data/bd_roster.json). Admin is implicit."""
    from backend.pipeline import bd_list
    return {"bds": [{"id": b["id"], "name": b["name"], "segment": b.get("segment", "")}
                    for b in bd_list.roster()]}


@app.get("/api/bd-list")
async def get_bd_list(month: str = "", authorization: str | None = Header(None)):
    """This month's frozen list, generated on first request (a minute or so -
    it reads the queue, Macro and the news archive). Every row: BD, instrument
    to pitch, play, reason, sources with read dates. `inputs` says which
    inputs were stale or unreachable when it was generated."""
    import re as _re
    if month and not _re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    from backend.pipeline import bd_list
    # Rows carry ACER's history with each name (lost deals, fees) - signed-in only.
    tok = _bearer(authorization)
    await _pipeline(lambda: pipeline_store.caller(tok))
    try:
        return await bd_list.get_or_generate(month or None)
    except pipeline_store.StoreUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/bd-list/regenerate")
async def regenerate_bd_list(authorization: str | None = Header(None)):
    """Build a new version of this month's list (the old one is kept). Needs a
    signed-in session when sign-in is on - the list is the team's month."""
    tok = _bearer(authorization)
    await _pipeline(lambda: pipeline_store.require_admin(tok))
    from backend.pipeline import bd_list
    try:
        return await bd_list.get_or_generate(force=True)
    except pipeline_store.StoreUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/me")
async def whoami(authorization: str | None = Header(None)):
    """Who the signed-in user is to ACER-IQ: Admin or not, and which BD
    profile their email maps to - so the app opens on the right view."""
    tok = _bearer(authorization)
    if not pipeline_store.per_user():
        return {"email": "", "admin": True, "profile": "", "mode": "local"}
    c = await _pipeline(lambda: pipeline_store.caller(tok))
    return {"email": c["email"], "admin": c["admin"], "profile": c["profile"],
            "admin_restricted": bool(settings.admin_emails.strip())}


# ── Macro (Tab 1): event -> sector -> named companies ────────────────────────

@app.get("/api/macro")
async def get_macro(days: int = 14):
    """Major macro events in the window, the sectors they hit, and the named
    companies in those sectors with debt maturing inside 9 months or thin
    interest coverage, ranked by winnability (backend/pipeline/macro.py).

    Degrades rather than 500s: a dead input is named in `coverage` and the
    list is built from what answered. Freshness rides along so an empty tab
    says whether it is a quiet fortnight or a dead feed."""
    if days < 1 or days > 90:
        raise HTTPException(status_code=400, detail="days must be between 1 and 90")
    from backend.pipeline import macro
    from backend.pipeline.rss_news import FEED_NAMES
    empty = {"events": [], "event_count": 0, "sectors": [], "named": 0,
             "window_days": days,
             "coverage": {"stale_inputs": ["macro build failed"],
                          "note": "Macro list could not be built - see server log."}}
    data = await _safe(macro.build(days), empty, "macro")
    feeds = list(_NEWS_SOURCES) + list(FEED_NAMES) + ["BSE"]
    fresh = [source_health.status(f) for f in feeds]
    return {**data, "freshness": fresh,
            "empty_means": source_health.summarise(fresh[:-1])}


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
    """Liveness AND per-source freshness for uptime checks - kept off "/" so the
    root can serve the UI.

    Process liveness alone proved nothing (PREMORTEM.md section 8): a dead
    scraper left this endpoint green. Now any source with no successful read
    for 48 hours, and any archive that would not survive a restart, lands in
    `degraded` - the list an uptime check pages on."""
    _failures.set(Counter())
    sources = _source_status(0) + _cra_status()
    # No LLM key is a supported mode, not a fault - PREMORTEM.md #6: every
    # LLM-produced field has a rule-based fallback, so this never blanks a
    # row. Still reported in `sources` (the per-search banner needs it) but
    # not paged on here, same treatment as the statically-blocked CRA sites.
    degraded = [s["name"] for s in sources if not s["ok"] and s["name"] != "AI scoring"]

    loaded = await asyncio.to_thread(source_health.ensure_loaded)
    fresh = await asyncio.to_thread(source_health.snapshot)
    degraded += [f"{f['source']}: {f['message']}" for f in fresh if f["alarm"]]
    if not loaded:
        degraded.append("source history not loaded - freshness below covers this process only")

    archives = {"cra_actions": await asyncio.to_thread(action_history.stats),
                "news": await asyncio.to_thread(news_archive.stats)}
    if database.supabase_configured():
        # Supabase is set up but an archive is still on SQLite: the tables were
        # not created, and every restart is silently wiping history.
        degraded += [f"{name} archive not durable (run supabase_schema.sql)"
                     for name, st in archives.items() if not st.get("durable")]
    if degraded:
        # Logged at WARNING so an always-on host's log alerting can fire on it
        # without anything having to poll this endpoint.
        log.warning("health: degraded sources %s", ", ".join(degraded))
    access = (f"Admin restricted to {settings.admin_emails.strip()}" if settings.admin_emails.strip()
              else "ADMIN_EMAILS not set - every signed-in user has Admin; keep Supabase sign-ups closed")
    return {
        "access": access,
        # "ok" still means the process is alive; "degraded" is the signal an
        # uptime check should page on, and it must not be buried in a sub-list.
        "status": "degraded" if degraded else "ok",
        "version": app.version,
        "ui": "bundled" if _FRONTEND_DIST.exists() else settings.frontend_url,
        "degraded": degraded,
        "sources": sources,
        "freshness": fresh,
        "archives": archives,
    }


# ── Poll: keep the archives filling when nobody has the app open ─────────────

_last_poll = {"at": 0.0}
_POLL_EVERY = 600   # seconds; a scheduled pinger cannot make this hammer NSE


@app.get("/api/poll")
async def poll():
    """Read every feed once and fold it into the archives.

    The archives only grow when something reads the feeds. Without this they
    grow only while a BD has the tab open, and a quiet week becomes a hole in
    the history. A scheduled job (.github/workflows/keepalive.yml) calls it;
    the throttle makes a second call inside ten minutes a cheap no-op."""
    import time as _time
    if _time.time() - _last_poll["at"] < _POLL_EVERY:
        return {"polled": False, "reason": "polled less than 10 minutes ago",
                "freshness": source_health.snapshot()}
    _last_poll["at"] = _time.time()
    await asyncio.to_thread(source_health.ensure_loaded)
    from backend.pipeline import bse_scraper, cra_press
    _news_polled["at"] = _time.time()
    # BSE too: the scrip master feeds Macro and refinance, and it is cached
    # for 12h, so this is a real read at most twice a day.
    nse, rss, cra, _ = await asyncio.gather(
        _safe(fetch_market_news(7), {}, "nse_news"),
        _safe(fetch_rss_news(), {}, "rss_news"),
        _safe(cra_press.fetch_recent_actions(days=30), {"actions": []}, "cra_press"),
        _safe(bse_scraper._load_master(), {}, "bse_instruments"))
    new_actions = await asyncio.to_thread(action_history.record, cra.get("actions", []))
    return {"polled": True,
            "news_new": (nse.get("archived_new") or 0) + (rss.get("archived_new") or 0),
            "cra_actions_new": new_actions,
            "freshness": source_health.snapshot()}


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
