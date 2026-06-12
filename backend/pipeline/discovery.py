import logging

import httpx

from backend.config import settings
from backend.netutil import SourceStatus, cache, limiter
from backend.registry.geo import CITY_COORDS as _CITY_COORDS

log = logging.getLogger("acer_iq.discovery")

PLACES_BASE  = "https://maps.googleapis.com/maps/api/place"
NOMINATIM    = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

GEOCODE_TTL = 7 * 24 * 3600
OVERPASS_TTL = 6 * 3600

_HEADERS = {"User-Agent": "ACER-IQ/2.0 (credit-rating-lead-tool; contact@acerratings.com)"}

_ENTITY_LABEL = {
    "Banks": "Bank", "NBFCs": "NBFC", "Corporates": "Corporate", "All": "Financial Entity",
}

# ── Major national/commercial banks — already rated by all agencies, not leads ─
# Only cooperative banks, small finance banks, RRBs, UCBs are worth targeting
_LARGE_BANKS_BLOCKLIST = {
    "state bank of india", "sbi", "hdfc bank", "icici bank", "axis bank",
    "kotak mahindra bank", "kotak bank", "punjab national bank", "pnb",
    "bank of baroda", "canara bank", "union bank of india", "union bank",
    "bank of india", "central bank of india", "indian bank", "indian overseas bank",
    "uco bank", "bank of maharashtra", "punjab & sind bank", "punjab and sind bank",
    "yes bank", "indusind bank", "federal bank", "south indian bank",
    "karnataka bank", "karur vysya bank", "city union bank", "dcb bank",
    "rbl bank", "bandhan bank", "idfc first bank", "idfc bank",
    "jammu and kashmir bank", "j&k bank", "nainital bank",
    "tamilnad mercantile bank", "lakshmi vilas bank", "dhanlaxmi bank",
    "hsbc", "citibank", "standard chartered", "deutsche bank", "barclays",
    "american express", "dbs bank", "abu dhabi commercial bank",
    "rajasthan marudhara gramin bank",  # example RRBs that are already well-rated
}

# City centroids come from backend.registry.geo (shared with the registry).


def _parse_city(location: str) -> str:
    return location.split(",")[0].strip()


# ── Geocoding ─────────────────────────────────────────────────────────────────

async def _nominatim(params: dict, status: SourceStatus | None) -> tuple[float, float] | None:
    try:
        async with limiter("geocode"):
            async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
                resp = await client.get(NOMINATIM, params=params)
        results = resp.json()
        if status:
            status.ok("geocode")
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as exc:
        log.warning("Nominatim geocode failed for %r: %r", params, exc)
        if status:
            status.fail("geocode", repr(exc))
    return None


async def _geocode(location: str, status: SourceStatus | None = None) -> tuple[float, float]:
    city = _parse_city(location).lower()
    if city in _CITY_COORDS:
        return _CITY_COORDS[city]

    key = "geocode:" + location.strip().lower()
    hit = cache.get(key)
    if hit is not None:
        return hit

    coords = await _nominatim({
        "q": f"{location}, India", "format": "json",
        "limit": 1, "countrycodes": "in",
    }, status)

    raw = location.strip()
    if coords is None and raw.isdigit() and len(raw) == 6:
        coords = await _nominatim({
            "postalcode": raw, "country": "India",
            "format": "json", "limit": 1,
        }, status)

    if coords is not None:
        cache.set(key, coords, GEOCODE_TTL)
        return coords
    return 20.5937, 78.9629


# ── Entity classification ─────────────────────────────────────────────────────

def _classify(tags: dict, fallback: str) -> str:
    amenity = tags.get("amenity", "")
    office  = tags.get("office", "")
    name    = tags.get("name", "").lower()

    if amenity == "bank":
        return "Bank"
    if any(w in name for w in ("gramin bank", "sahakari bank", "co-operative bank",
                                "cooperative bank", "urban bank", "rural bank")):
        return "Bank"
    if office in ("financial", "insurance", "moneylender"):
        return "NBFC"
    if any(w in name for w in ("finance", "capital", "nbfc", "housing finance",
                                "mutual fund", "microfinance", "micro finance",
                                "leasing", "credit corp", "fincorp", "asset management",
                                "securities", "investment")):
        return "NBFC"
    if any(w in name for w in ("limited", "ltd", "pvt", "corporation", "holdings",
                                "industries", "group", "infrastructure", "enterprises",
                                "technologies", "power", "steel", "cement", "pharma",
                                "energy", "chemicals", "textiles")):
        return "Corporate"
    return fallback


def _address(tags: dict) -> str:
    parts = []
    for k in ("addr:housename", "addr:housenumber", "addr:street",
              "addr:suburb", "addr:city", "addr:state"):
        v = tags.get(k, "").strip()
        if v:
            parts.append(v)
    return ", ".join(parts)


# ── Entity-specific Overpass queries ─────────────────────────────────────────

def _build_overpass_query(lat: float, lng: float, entity_type: str, radius: int) -> str:
    """Separate query per entity type — prevents bank-heavy OSM data from
    drowning out NBFCs and Corporates in mixed results."""

    if entity_type == "Banks":
        # Head offices of cooperative banks, small finance banks, RRBs, UCBs only.
        # Using office=bank / office=financial tags which OSM uses for HQ-level entries,
        # plus name-pattern search. Branches are tagged amenity=bank — we exclude those.
        return f"""[out:json][timeout:30];
(
  node["office"="bank"](around:{radius},{lat},{lng});
  way["office"="bank"](around:{radius},{lat},{lng});
  node["office"="financial"]["name"~"Bank",i](around:{radius},{lat},{lng});
  way["office"="financial"]["name"~"Bank",i](around:{radius},{lat},{lng});
  node["headquarters"="yes"]["amenity"="bank"](around:{radius},{lat},{lng});
  way["headquarters"="yes"]["amenity"="bank"](around:{radius},{lat},{lng});
  node["name"~"Gramin Bank|Sahakari Bank|Co-operative Bank|Cooperative Bank|Urban Co-op|Small Finance Bank|Nagrik Bank|Nagarik Bank|Janata Bank|Mahila Bank|Nagar Bank",i]["name"](around:{radius},{lat},{lng});
  way["name"~"Gramin Bank|Sahakari Bank|Co-operative Bank|Cooperative Bank|Urban Co-op|Small Finance Bank|Nagrik Bank|Nagarik Bank|Janata Bank|Mahila Bank|Nagar Bank",i]["name"](around:{radius},{lat},{lng});
);
out center 60;"""

    elif entity_type == "NBFCs":
        # office=financial tags in OSM are HQ-level; name patterns catch the rest
        return f"""[out:json][timeout:30];
(
  node["office"~"financial|insurance|moneylender"](around:{radius},{lat},{lng});
  way["office"~"financial|insurance|moneylender"](around:{radius},{lat},{lng});
  node["name"~"Finance Ltd|Finance Limited|Capital Ltd|Capital Limited|NBFC|Housing Finance|Microfinance|Micro Finance|Leasing|Fincorp|Finserv|Securities Ltd|Asset Management|Investment Fund",i]["name"](around:{radius},{lat},{lng});
  way["name"~"Finance Ltd|Finance Limited|Capital Ltd|Capital Limited|NBFC|Housing Finance|Microfinance|Micro Finance|Leasing|Fincorp|Finserv|Securities Ltd|Asset Management|Investment Fund",i]["name"](around:{radius},{lat},{lng});
);
out center 60;"""

    elif entity_type == "Corporates":
        # office=company in OSM is typically registered office / HQ
        return f"""[out:json][timeout:30];
(
  node["office"="company"](around:{radius},{lat},{lng});
  way["office"="company"](around:{radius},{lat},{lng});
  node["name"~"Industries Ltd|Industries Limited|Infrastructure Ltd|Corporation Ltd|Holdings Ltd|Enterprises Ltd|Technologies Ltd|Power Ltd|Steel Ltd|Cement Ltd|Pharma Ltd|Energy Ltd|Chemicals Ltd|Textiles Ltd|Constructions Ltd",i]["name"](around:{radius},{lat},{lng});
  way["name"~"Industries Ltd|Industries Limited|Infrastructure Ltd|Corporation Ltd|Holdings Ltd|Enterprises Ltd|Technologies Ltd|Power Ltd|Steel Ltd|Cement Ltd|Pharma Ltd|Energy Ltd|Chemicals Ltd|Textiles Ltd|Constructions Ltd",i]["name"](around:{radius},{lat},{lng});
);
out center 60;"""

    else:  # All — pull HQ-tagged offices across all types
        return f"""[out:json][timeout:30];
(
  node["office"~"bank|financial|insurance|company"](around:{radius},{lat},{lng});
  way["office"~"bank|financial|insurance|company"](around:{radius},{lat},{lng});
  node["name"~"Finance Ltd|Finance Limited|Capital Ltd|Industries Ltd|Infrastructure Ltd|Corporation Ltd|Holdings Ltd|Technologies Ltd|Gramin Bank|Sahakari Bank|Co-operative Bank|Small Finance Bank",i]["name"](around:{radius},{lat},{lng});
  way["name"~"Finance Ltd|Finance Limited|Capital Ltd|Industries Ltd|Infrastructure Ltd|Corporation Ltd|Holdings Ltd|Technologies Ltd|Gramin Bank|Sahakari Bank|Co-operative Bank|Small Finance Bank",i]["name"](around:{radius},{lat},{lng});
);
out center 60;"""


# ── Overpass search ───────────────────────────────────────────────────────────

async def _overpass_search(lat: float, lng: float, entity_type: str,
                           radius: int = 15000,
                           status: SourceStatus | None = None) -> list[dict]:
    entity_label = _ENTITY_LABEL.get(entity_type, "Financial Entity")
    q = _build_overpass_query(lat, lng, entity_type, radius)

    key = f"overpass:{entity_type}:{lat:.3f}:{lng:.3f}:{radius}"
    cached_result = cache.get(key)
    if cached_result is not None:
        if status:
            status.ok("overpass")
        return cached_result

    try:
        async with limiter("overpass"):
            async with httpx.AsyncClient(timeout=35, headers=_HEADERS) as client:
                resp = await client.post(OVERPASS_URL, data={"data": q})
        data = resp.json()
        if status:
            status.ok("overpass")
    except Exception as exc:
        log.warning("Overpass query failed (%s, r=%d): %r", entity_type, radius, exc)
        if status:
            status.fail("overpass", repr(exc))
        return []

    companies: list[dict] = []
    seen: set[str] = set()

    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name", tags.get("operator", "")).strip()
        if not name or name.lower() in seen:
            continue

        # Skip branches, ATMs, kiosks — we want head offices only
        name_lower = name.lower()
        _branch_indicators = (
            " branch", " br.", " br ", "- branch", "– branch",
            " atm", "kiosk", "extension counter", "extension office",
            " regional office", " zonal office", " circle office",
            " divisional office", " district office", " sub office",
            " service branch", " main branch", " city branch",
            " urban branch", " rural branch", " micro branch",
        )
        if any(ind in name_lower for ind in _branch_indicators):
            continue

        entity = _classify(tags, entity_label)

        # Strict entity filter per mode
        if entity_type == "Banks" and entity != "Bank":
            continue
        if entity_type == "NBFCs" and entity != "NBFC":
            continue
        if entity_type == "Corporates" and entity != "Corporate":
            continue

        # Skip large national/commercial banks — already rated by all agencies
        # ACER targets: cooperative banks, small finance banks, RRBs, UCBs
        if entity == "Bank":
            if any(bl in name_lower for bl in _LARGE_BANKS_BLOCKLIST):
                continue
            # Also skip if it's clearly a branch of a major bank
            major_bank_keywords = (
                "sbi ", "hdfc ", "icici ", "axis ", "kotak ", "pnb ",
                "canara ", "union bank", "bank of baroda", "bank of india",
                "yes bank", "indusind", "federal bank",
            )
            if any(name_lower.startswith(kw) for kw in major_bank_keywords):
                continue

        seen.add(name_lower)

        if el["type"] == "node":
            clat, clng = float(el["lat"]), float(el["lon"])
        else:
            c = el.get("center", {})
            if not c:
                continue
            clat, clng = float(c["lat"]), float(c["lon"])

        companies.append({
            "id":          f"osm_{el['id']}",
            "name":        name,
            "address":     _address(tags),
            "lat":         clat,
            "lng":         clng,
            "website":     tags.get("website", tags.get("url", "")),
            "phone":       tags.get("phone", tags.get("contact:phone", "")),
            "entity_type": entity,
        })

    cache.set_result(key, companies, OVERPASS_TTL)
    return companies


# ── Public entry point ────────────────────────────────────────────────────────

def _is_pincode(s: str) -> bool:
    return s.strip().isdigit() and len(s.strip()) == 6


async def discover_companies(
    city: str,
    industry: str = "",
    entity_type: str = "All",
    instrument_type: str = "All",
    status: SourceStatus | None = None,
) -> tuple[list[dict], float, float]:
    lat, lng = await _geocode(city, status)

    # ── Primary: RBI registry (complete universe of head offices) ────────────
    # Banks → UCBs + SFBs; NBFCs → RBI-registered NBFCs/ARCs.
    # Corporates are not in the registry yet (MCA ingest pending) — they still
    # go through OSM/Places below.
    from backend.registry import store as registry_store
    companies: list[dict] = []
    if entity_type in ("Banks", "NBFCs", "All"):
        if registry_store.available():
            companies = registry_store.search(city, entity_type, limit=60)
            if status:
                status.ok("rbi_registry")
        elif status:
            status.fail("rbi_registry", "registry.sqlite missing — run backend.registry.ingest")
        log.info("registry: %d head offices for %r (%s)", len(companies), city, entity_type)

    # ── Secondary: OSM/Places for Corporates (or registry miss) ─────────────
    need_osm = (
        entity_type == "Corporates"
        or (entity_type == "All")
        or not companies
    )
    if need_osm:
        osm_type = "Corporates" if entity_type == "All" and companies else entity_type
        osm = await _overpass_search(lat, lng, osm_type, radius=15000, status=status)
        if len(companies) + len(osm) < 8:
            wider = await _overpass_search(lat, lng, osm_type, radius=30000, status=status)
            seen_osm = {c["name"].lower() for c in osm}
            osm += [c for c in wider if c["name"].lower() not in seen_osm]
        log.info("overpass: %d companies for %r (%s)", len(osm), city, osm_type)

        seen = {c["name"].lower() for c in companies}
        for c in osm:
            if c["name"].lower() not in seen:
                c["discovery_source"] = "osm"
                companies.append(c)
                seen.add(c["name"].lower())

    # Google Places fallback if still empty
    if not companies:
        api_key = settings.google_places_api_key
        if api_key and api_key not in ("your_key_here", ""):
            companies = await _google_places_search(city, entity_type, api_key, lat, lng,
                                                    status=status)
        elif status:
            status.skip("google_places", "no API key configured")

    limit = 60 if any(c.get("discovery_source") == "rbi_registry" for c in companies) else 30
    return companies[:limit], lat, lng


async def _google_places_search(city, entity_type, api_key, lat, lng,
                                status: SourceStatus | None = None):
    _Q = {
        "Banks":      "bank",
        "NBFCs":      "NBFC finance company",
        "Corporates": "corporate office company",
        "All":        "bank finance company",
    }
    location_term = f"pincode {city}" if _is_pincode(city) else city
    query = f"{_Q.get(entity_type, 'bank')} in {location_term} India"
    el = _ENTITY_LABEL.get(entity_type, "Financial Entity")
    companies = []
    try:
        async with limiter("places"):
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{PLACES_BASE}/textsearch/json",
                    params={"query": query, "key": api_key, "type": "establishment", "region": "in"},
                )
        if status:
            status.ok("google_places")
        for i, p in enumerate(resp.json().get("results", [])[:15]):
            loc = p.get("geometry", {}).get("location", {})
            companies.append({
                "id":          p.get("place_id", f"gp_{i}"),
                "name":        p.get("name", "?"),
                "address":     p.get("formatted_address", ""),
                "lat":         loc.get("lat", lat),
                "lng":         loc.get("lng", lng),
                "website":     "",
                "phone":       "",
                "entity_type": el,
            })
    except Exception as exc:
        log.warning("Google Places search failed for %r: %r", query, exc)
        if status:
            status.fail("google_places", repr(exc))
    return companies
