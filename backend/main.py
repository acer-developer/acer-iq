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
from backend.pipeline.bse_scraper import fetch_past_instruments
from backend.pipeline.office_locator import find_office_locations
from backend.pipeline.credit_history import fetch_credit_history
from backend.pipeline.fit_analyzer import analyze_fit
from backend import database

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
        req.city, industry, entity_type, instrument_type
    )
    if not raw_companies:
        raise HTTPException(
            status_code=404,
            detail=f"No companies found in {req.city} for {entity_type}",
        )

    # Enrich contacts — safe, returns companies with empty contacts on failure
    try:
        raw_companies = await enrich_contacts(raw_companies)
    except Exception:
        for c in raw_companies:
            c.setdefault("contacts", [])

    async def _enrich_one(c: dict) -> dict:
        # MCA data
        mca = await _safe(fetch_mca_data(c["name"]), {})
        c.update({
            "cin": mca.get("cin", ""),
            "incorporation_date": mca.get("incorporation_date", ""),
            "directors": mca.get("directors", []),
        })
        if not c.get("address") and mca.get("registered_address"):
            c["address"] = mca["registered_address"]

        # BSE instruments
        instruments = await _safe(fetch_past_instruments(c["name"]), [])
        c["past_instruments"] = instruments

        # AI scoring
        c = await _safe(
            score_company(c, industry, req.city, c.get("entity_type", entity_type), instrument_type),
            c,
        )
        c.setdefault("score", 0)
        c.setdefault("score_label", "Pending")
        c.setdefault("why_quality_lead", [])
        c.setdefault("pain_points", [])
        c.setdefault("recommended_approach", "")

        # Office locations
        offices = await _safe(find_office_locations(c["name"], c["lat"], c["lng"]), [])
        c["office_locations"] = offices

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
    except Exception:
        pass

    return {"suggestions": suggestions}


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

    # Fetch MCA data
    company_info: dict = {}
    if is_cin:
        company_info["cin"] = query
        company_info["name"] = query  # will be enriched below
    else:
        company_info["name"] = query

    mca = await _safe(fetch_mca_data(query), {})
    company_info.update({
        "name": mca.get("name", query),
        "cin": mca.get("cin", query if is_cin else ""),
        "address": mca.get("registered_address", "India"),
        "incorporation_date": mca.get("incorporation_date", ""),
        "directors": mca.get("directors", []),
        "entity_type": _guess_entity_type(query),
    })

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
