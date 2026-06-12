import asyncio
import csv
import io
import json
import logging
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import settings
from backend.models import (
    Company, Contact, Director, OfficeLocation, PastInstrument,
    SearchRequest, SearchResponse,
)
from backend.netutil import SourceStatus, setup_logging
from backend.pipeline.discovery import discover_companies
from backend.pipeline.enricher import enrich_contacts
from backend.pipeline.mca_scraper import fetch_mca_data, is_cin
from backend.pipeline.scorer import score_companies
from backend.pipeline.bse_scraper import fetch_past_instruments
from backend.pipeline.office_locator import find_office_locations
from backend.pipeline.credit_history import fetch_credit_history
from backend.pipeline.fit_analyzer import analyze_fit
from backend import database
from backend.registry import store as registry_store

setup_logging()
log = logging.getLogger("acer_iq.api")

app = FastAPI(title="ACER-IQ", version="2.0.0")

_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_search_cache: dict[str, list[dict]] = {}


async def _safe(coro, default, label: str = ""):
    """Run a coroutine, log (never swallow silently) and return default on error."""
    try:
        return await coro
    except Exception as exc:
        log.warning("%s failed: %r", label or "pipeline step", exc)
        return default


@app.get("/api/health")
async def health():
    return {"app": "ACER-IQ", "registry": registry_store.stats()}


@app.post("/api/search", response_model=SearchResponse)
async def search_leads(req: SearchRequest):
    if not req.city.strip():
        raise HTTPException(status_code=400, detail="City or pincode is required")

    entity_type = req.entity_type or "All"
    instrument_type = req.instrument_type or "All"
    industry = req.industry or f"{entity_type} — {instrument_type}"
    status = SourceStatus()

    log.info("search: city=%r entity=%s instrument=%s", req.city, entity_type, instrument_type)
    raw_companies, city_lat, city_lng = await discover_companies(
        req.city, industry, entity_type, instrument_type, status=status
    )
    if not raw_companies:
        # Return empty result with city coordinates so map still zooms
        return SearchResponse(
            companies=[], city_lat=city_lat, city_lng=city_lng,
            search_id=str(uuid.uuid4()), sources=status.as_dict(),
        )

    # Enrich contacts — safe, returns companies with empty contacts on failure
    try:
        raw_companies = await enrich_contacts(raw_companies, status=status)
    except Exception as exc:
        log.warning("contact enrichment failed: %r", exc)
        for c in raw_companies:
            c.setdefault("contacts", [])

    # Per-search throttle on top of the per-source limiters in netutil
    _sem = asyncio.Semaphore(8)

    async def _enrich_one(c: dict) -> dict:
        async with _sem:
            # MCA data — never overwrite registry values with blanks.
            # Registry rows already carry an RBI-verified CIN: skip the
            # per-lead master-data lookup entirely (saves up to 3 external
            # calls per lead; directors load on company drill-down instead).
            if c.get("cin"):
                mca = {}
            else:
                mca = await _safe(fetch_mca_data(c["name"], status=status), {},
                                  f"mca({c['name']})")
            c["cin"] = c.get("cin") or mca.get("cin", "")
            c["incorporation_date"] = mca.get("incorporation_date", "")
            c["listing_date"] = mca.get("listing_date", "")
            c["directors"] = mca.get("directors", [])
            if not c.get("address") and mca.get("registered_address"):
                c["address"] = mca["registered_address"]

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

            # BSE instruments
            instruments = await _safe(fetch_past_instruments(c["name"], status=status),
                                      [], f"bse_instruments({c['name']})")
            c["past_instruments"] = instruments

            # Office locations: fetched on demand via /api/offices when a lead
            # is selected — not in bulk (60 Google calls per search)
            c["office_locations"] = []

            return c

    enriched = list(await asyncio.gather(*[_enrich_one(c) for c in raw_companies]))

    # Scoring: rule-based per company + ONE batched LLM call for the search
    enriched = await _safe(
        score_companies(enriched, industry, req.city, instrument_type, status=status),
        enriched, "batch scoring",
    )
    for c in enriched:
        c.setdefault("score", 0)
        c.setdefault("score_label", "Pending")
        c.setdefault("why_quality_lead", [])
        c.setdefault("pain_points", [])
        c.setdefault("recommended_approach", "")

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
            listing_date=c.get("listing_date", ""),
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
    try:
        database.save_search(search_id, req.city, industry, companies)
    except Exception as exc:
        log.warning("supabase save_search failed: %r", exc)

    sources = status.as_dict()
    log.info("search done: %d companies, sources=%s", len(companies),
             {k: v["status"] for k, v in sources.items()})
    return SearchResponse(
        companies=companies,
        city_lat=city_lat,
        city_lng=city_lng,
        search_id=search_id,
        sources=sources,
    )


# ── Company Autocomplete ─────────────────────────────────────────────────────

@app.get("/api/company-suggest")
async def company_suggest(q: str = ""):
    """Return BSE-listed company suggestions matching the query."""
    if len(q.strip()) < 2:
        return {"suggestions": []}

    _headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bseindia.com/",
        "Accept": "application/json, text/plain, */*",
    }

    suggestions = []
    try:
        async with httpx.AsyncClient(timeout=6, headers=_headers) as client:
            resp = await client.get(
                "https://api.bseindia.com/BseIndiaAPI/api/SearchData/w",
                params={
                    "strText": q.strip(),
                    "flag": "0",
                    "Membertype": "S",
                    "pageno": "1",
                    "tab": "ALL",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in (data.get("Table") or [])[:20]:
                    name = (item.get("Scrip_Name") or item.get("SCRIP_NAME") or "").strip()
                    code = str(item.get("SCRIP_CD") or item.get("scripCd") or "")
                    sector = (item.get("SECTOR") or item.get("Sector") or "").strip()
                    if name:
                        suggestions.append({
                            "name": name,
                            "bse_code": code,
                            "sector": sector,
                        })
    except Exception as exc:
        log.warning("BSE suggest failed for %r: %r", q, exc)

    return {"suggestions": suggestions}


# ── Company Credit History ────────────────────────────────────────────────────

class CompanyCreditRequest(BaseModel):
    query: str  # company name or CIN


@app.post("/api/company-credit")
async def company_credit(req: CompanyCreditRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Company name or CIN is required")

    query = req.query.strip()
    status = SourceStatus()
    query_is_cin = is_cin(query)

    company_info: dict = {"name": query, "cin": query.upper() if query_is_cin else ""}

    # P6 fix: a CIN must never be fed into the BSE *name*-search endpoints.
    # Resolve it to a company name first — instantly via the RBI registry
    # (covers all 9k+ NBFCs/ARCs), with Zauba as the online fallback.
    if query_is_cin:
        reg = registry_store.by_cin(query)
        if reg:
            status.ok("rbi_registry")
            company_info.update({
                "name": reg["name"],
                "address": reg.get("address") or "India",
                "entity_type": "Bank" if reg["entity_type"] == "Bank" else "NBFC",
            })

    # MCA master data: fetch_mca_data routes CINs to Zauba, names to BSE→Zauba
    mca = await _safe(fetch_mca_data(query, status=status), {}, "mca lookup")
    company_info.update({
        "name": mca.get("name") or company_info.get("name", query),
        "cin": mca.get("cin") or company_info.get("cin", ""),
        "address": mca.get("registered_address") or company_info.get("address", "India"),
        "incorporation_date": mca.get("incorporation_date", ""),
        "listing_date": mca.get("listing_date", ""),
        "directors": mca.get("directors", []),
    })
    company_info.setdefault("entity_type", _guess_entity_type(company_info["name"]))

    # Fetch credit history from BSE — always by *name*, never by CIN
    search_name = company_info["name"]
    if query_is_cin and search_name == query:
        log.warning("could not resolve CIN %s to a company name", query)
        credit_data = {"agencies": [], "total_instruments": 0,
                       "rated_by_count": 0, "raw_instruments": []}
    else:
        credit_data = await _safe(fetch_credit_history(search_name, status=status), {
            "agencies": [],
            "total_instruments": 0,
            "rated_by_count": 0,
            "raw_instruments": [],
        }, "credit history")

    # AI fit analysis
    fit = await _safe(analyze_fit(company_info, credit_data, status=status), {
        "fit_score": 0,
        "fit_label": "Pending",
        "opportunity_type": "Analysis unavailable",
        "key_insights": [],
        "watch_outs": [],
        "recommended_action": "Configure OPENROUTER_API_KEY for AI analysis",
        "best_instrument_pitch": "NCD",
        "urgency": "Medium",
        "already_rated_by_infomerics": False,
    }, "fit analysis")

    return {
        "company": company_info,
        "credit_data": credit_data,
        "fit_analysis": fit,
        "sources": status.as_dict(),
    }


def _guess_entity_type(name: str) -> str:
    n = name.upper()
    if any(w in n for w in ["BANK", "BANKING"]):
        return "Bank"
    if any(w in n for w in ["NBFC", "FINANCE", "FINSERV", "CAPITAL", "LEASING", "HOUSING"]):
        return "NBFC"
    return "Corporate"


# ── Offices & Instruments on demand ──────────────────────────────────────────

@app.get("/api/offices/{company_name}")
async def get_offices(company_name: str, lat: float = 20.5937, lng: float = 78.9629):
    offices = await _safe(find_office_locations(company_name, lat, lng), [], "office lookup")
    return {"offices": offices}


@app.get("/api/instruments/{company_name}")
async def get_instruments(company_name: str):
    instruments = await _safe(fetch_past_instruments(company_name), [], "instrument lookup")
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
        except Exception:
            pass
    if not cached:
        raise HTTPException(status_code=404, detail="Search not found")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Name", "Entity Type", "Score", "Score Label", "Address",
        "Website", "Phone", "CIN", "Incorporated", "BSE Listed",
        "Past Instruments Count", "Directors", "Contacts",
        "Why Quality Lead", "Pain Points", "Recommended Approach",
    ])
    for c in cached:
        writer.writerow([
            c.get("name"), c.get("entity_type"), c.get("score"), c.get("score_label"),
            c.get("address"), c.get("website"), c.get("phone"), c.get("cin"),
            c.get("incorporation_date"), c.get("listing_date", ""),
            len(c.get("past_instruments", [])),
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


# ── Serve React build in production ──────────────────────────────────────────
_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="static")
