import asyncio
import csv
import io
import json
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
from backend import database
from backend.registry import store as registry_store

app = FastAPI(title="Lead Gen Tool", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_search_cache: dict[str, list[dict]] = {}


async def _safe(coro, default):
    """Run a coroutine and return default on any exception."""
    try:
        return await coro
    except Exception:
        return default


@app.post("/api/search", response_model=SearchResponse)
async def search_leads(req: SearchRequest):
    if not req.city.strip():
        raise HTTPException(status_code=400, detail="City or pincode is required")

    entity_type = req.entity_type or "All"
    instrument_type = req.instrument_type or "All"
    industry = req.industry or f"{entity_type} — {instrument_type}"

    raw_companies, city_lat, city_lng = await discover_companies(
        req.city, industry, entity_type, instrument_type, size=req.size or "All"
    )
    if not raw_companies:
        # Return empty result with city coordinates so map still zooms
        return SearchResponse(
            companies=[], city_lat=city_lat, city_lng=city_lng,
            search_id=str(uuid.uuid4()),
        )

    # Enrich contacts — safe, returns companies with empty contacts on failure
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

            # MCA data — skipped when we already know the CIN (registry NBFCs:
            # incorporation year is encoded in the CIN itself) and for co-op
            # banks (cooperative societies, not MCA companies at all).
            # Directors load on click via Company Research.
            if c.get("cin"):
                year = c["cin"][8:12]
                c["incorporation_date"] = year if year.isdigit() else ""
                c.setdefault("directors", [])
            elif not is_coop:
                mca = await _safe(fetch_mca_data(c["name"], skip_zauba=True), {})
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

            # BSE instruments — non-scheduled co-op banks never list debt on
            # BSE; skip 4 wasted searches each
            if sub == "Co-operative Bank":
                c["past_instruments"] = []
            else:
                c["past_instruments"] = await _safe(fetch_past_instruments(c["name"]), [])

            # Scoring: rule-based only in bulk search (fast, deterministic).
            # The LLM still powers fit analysis in Company Research.
            c = await _safe(
                score_company(c, industry, req.city, c.get("entity_type", entity_type),
                              instrument_type, use_llm=False),
                c,
            )
            c.setdefault("score", 0)
            c.setdefault("score_label", "Pending")
            c.setdefault("why_quality_lead", [])
            c.setdefault("pain_points", [])
            c.setdefault("recommended_approach", "")

            # Office locations: fetched on demand via /api/offices when a lead
            # is selected — not in bulk (60 Google calls per search)
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
    try:
        database.save_search(search_id, req.city, industry, companies)
    except Exception:
        pass

    return SearchResponse(
        companies=companies,
        city_lat=city_lat,
        city_lng=city_lng,
        search_id=search_id,
    )


# ── Company Autocomplete ─────────────────────────────────────────────────────

@app.get("/api/company-suggest")
async def company_suggest(q: str = ""):
    """Company suggestions: RBI registry first (10,500+ entities, instant,
    includes CIN), BSE-listed companies appended when BSE is reachable."""
    if len(q.strip()) < 2:
        return {"suggestions": []}

    suggestions = registry_store.suggest(q.strip(), limit=8)
    seen = {s["name"].lower() for s in suggestions}

    # Supplement with BSE-listed companies (covers corporates outside RBI lists)
    from backend.pipeline.bse_scraper import _bse_tripped, get_bse_client
    if not _bse_tripped():
        try:
            resp = await get_bse_client().get(
                "https://api.bseindia.com/BseIndiaAPI/api/SearchData/w",
                params={
                    "strText": q.strip(), "flag": "0",
                    "Membertype": "S", "pageno": "1", "tab": "ALL",
                },
            )
            if resp.status_code == 200:
                for item in (resp.json().get("Table") or [])[:10]:
                    name = (item.get("Scrip_Name") or item.get("SCRIP_NAME") or "").strip()
                    code = str(item.get("SCRIP_CD") or item.get("scripCd") or "")
                    sector = (item.get("SECTOR") or item.get("Sector") or "").strip()
                    if name and name.lower() not in seen:
                        suggestions.append({
                            "name": name, "cin": "", "bse_code": code,
                            "sector": sector or "BSE-listed",
                            "source": "bse",
                        })
                        seen.add(name.lower())
        except Exception:
            pass

    return {"suggestions": suggestions[:15]}


# ── Company Credit History ────────────────────────────────────────────────────

class CompanyCreditRequest(BaseModel):
    query: str  # company name or CIN


@app.post("/api/company-credit")
async def company_credit(req: CompanyCreditRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Company name or CIN is required")

    query = req.query.strip()

    # Detect CIN pattern (starts with L or U followed by digits and letters)
    is_cin = len(query) == 21 and query[0].upper() in ("L", "U")

    # 1) RBI registry — authoritative for NBFCs / co-op banks / SFBs / ARCs
    reg = registry_store.get_by_name(query)

    company_info: dict = {
        "name": (reg or {}).get("name") or query,
        "cin": (reg or {}).get("cin") or (query if is_cin else ""),
        "address": (reg or {}).get("address", ""),
        "email": (reg or {}).get("email", ""),
        "entity_type": (reg or {}).get("entity_type") or _guess_entity_type(query),
        "sub_type": (reg or {}).get("sub_type", ""),
        "layer": (reg or {}).get("layer", ""),
        "deposit_taking": (reg or {}).get("deposit_taking", False),
        "data_source": "RBI registry" if reg else "",
        "incorporation_date": "",
        "directors": [],
    }
    if company_info["cin"] and len(company_info["cin"]) == 21:
        year = company_info["cin"][8:12]
        if year.isdigit():
            company_info["incorporation_date"] = year

    # 2) BSE/Zauba — listing data, directors, registered address
    mca = await _safe(fetch_mca_data(company_info["name"]), {})
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

    # Fetch credit history from BSE
    search_name = company_info["name"] if company_info["name"] != query else query
    credit_data = await _safe(fetch_credit_history(search_name), {
        "agencies": [],
        "total_instruments": 0,
        "rated_by_count": 0,
        "raw_instruments": [],
    })

    # AI fit analysis
    fit = await _safe(analyze_fit(company_info, credit_data), {
        "fit_score": 0,
        "fit_label": "Pending",
        "opportunity_type": "Analysis unavailable",
        "key_insights": [],
        "watch_outs": [],
        "recommended_action": "Run with Anthropic API key for AI analysis",
        "best_instrument_pitch": "NCD",
        "urgency": "Medium",
        "already_rated_by_infomerics": False,
    })

    return {
        "company": company_info,
        "credit_data": credit_data,
        "fit_analysis": fit,
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
    """Board of directors for a selected lead — BSE CorpInfo for listed
    companies, Zauba for private ones. Cached + circuit-breaker protected."""
    mca = await _safe(fetch_mca_data(company_name), {})
    return {
        "directors": mca.get("directors", []),
        "cin": mca.get("cin", ""),
        "incorporation_date": mca.get("incorporation_date", ""),
    }


# ── Offices & Instruments on demand ──────────────────────────────────────────

@app.get("/api/offices/{company_name}")
async def get_offices(company_name: str, lat: float = 20.5937, lng: float = 78.9629):
    offices = await _safe(find_office_locations(company_name, lat, lng), [])
    return {"offices": offices}


@app.get("/api/instruments/{company_name}")
async def get_instruments(company_name: str):
    instruments = await _safe(fetch_past_instruments(company_name), [])
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


# ── Serve React build in production ──────────────────────────────────────────
_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="static")
