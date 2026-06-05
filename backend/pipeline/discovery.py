import httpx
from backend.config import settings

PLACES_BASE   = "https://maps.googleapis.com/maps/api/place"
NOMINATIM     = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL  = "https://overpass-api.de/api/interpreter"

_HEADERS = {"User-Agent": "CredSight/2.0 (credit-rating-lead-tool; contact@acerratings.com)"}

# ── Geocoding ────────────────────────────────────────────────────────────────

async def _geocode(location: str) -> tuple[float, float]:
    """Geocode a city or 6-digit pincode in India via Nominatim."""
    try:
        async with httpx.AsyncClient(timeout=8, headers=_HEADERS) as client:
            resp = await client.get(NOMINATIM, params={
                "q": f"{location}, India",
                "format": "json",
                "limit": 1,
                "countrycodes": "in",
            })
            results = resp.json()
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        pass
    return 20.5937, 78.9629   # India centre fallback

# ── OpenStreetMap / Overpass API (real data, no API key) ─────────────────────

# Overpass filter tags per entity type
_OVERPASS_TAGS = {
    "Banks": [
        '["amenity"="bank"]',
    ],
    "NBFCs": [
        '["office"~"financial|moneylender|insurance|microfinance"]',
        '["amenity"~"microfinance|money_transfer"]',
    ],
    "Corporates": [
        '["office"~"company|commercial|government|ngo"]',
    ],
    "All": [
        '["amenity"="bank"]',
        '["office"~"financial|company|commercial|moneylender|insurance"]',
    ],
}

_ENTITY_LABEL: dict[str, str] = {
    "Banks":      "Bank",
    "NBFCs":      "NBFC",
    "Corporates": "Corporate",
    "All":        "Financial Entity",
}


def _entity_from_tags(tags: dict, fallback: str) -> str:
    amenity = tags.get("amenity", "")
    office  = tags.get("office", "")
    if amenity == "bank":
        return "Bank"
    if office in ("financial", "moneylender", "insurance", "microfinance"):
        return "NBFC"
    if office in ("company", "commercial", "government", "ngo"):
        return "Corporate"
    return fallback


def _address_from_tags(tags: dict, city: str) -> str:
    parts = []
    for key in ("addr:housename", "addr:housenumber", "addr:street",
                "addr:suburb", "addr:city", "addr:state"):
        v = tags.get(key, "").strip()
        if v:
            parts.append(v)
    return ", ".join(parts) if parts else city


async def _overpass_search(lat: float, lng: float,
                           entity_type: str, radius_m: int = 15000) -> list[dict]:
    """Query Overpass API for real financial entities within radius of a point."""
    tags_list = _OVERPASS_TAGS.get(entity_type, _OVERPASS_TAGS["All"])
    entity_label = _ENTITY_LABEL.get(entity_type, "Financial Entity")

    # Build union of node + way queries
    parts = []
    for tag in tags_list:
        parts.append(f'  node{tag}(around:{radius_m},{lat},{lng});')
        parts.append(f'  way{tag}(around:{radius_m},{lat},{lng});')

    query = f"""[out:json][timeout:20];
(
{chr(10).join(parts)}
);
out center 15;"""

    try:
        async with httpx.AsyncClient(timeout=20, headers=_HEADERS) as client:
            resp = await client.post(OVERPASS_URL, data={"data": query})
            data = resp.json()
    except Exception:
        return []

    companies: list[dict] = []
    seen: set[str] = set()

    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name", tags.get("operator", "")).strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

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
            "address":     _address_from_tags(tags, ""),
            "lat":         clat,
            "lng":         clng,
            "website":     tags.get("website", tags.get("url", "")),
            "phone":       tags.get("phone", tags.get("contact:phone", "")),
            "entity_type": _entity_from_tags(tags, entity_label),
        })

        if len(companies) >= 10:
            break

    return companies

# ── Google Places API ────────────────────────────────────────────────────────

_ENTITY_QUERIES: dict[str, str] = {
    "Banks":      "scheduled commercial bank private sector bank",
    "NBFCs":      "NBFC non banking financial company housing finance",
    "Corporates": "corporate company head office listed company",
    "All":        "bank NBFC financial company corporate",
}

_INSTRUMENT_HINT: dict[str, str] = {
    "NCD":  "NCD debentures",
    "Bond": "bond issuer",
    "IPO":  "IPO listed",
    "Debt": "debt financing",
    "All":  "",
}


def _is_pincode(location: str) -> bool:
    return location.strip().isdigit() and len(location.strip()) == 6


# ── Public entry point ───────────────────────────────────────────────────────

async def discover_companies(
    city: str,
    industry: str = "",
    entity_type: str = "All",
    instrument_type: str = "All",
) -> tuple[list[dict], float, float]:
    """
    Find financial entities in a city/pincode.
    Uses Google Places if API key is set, otherwise falls back to
    OpenStreetMap Overpass API (real data, no hallucination, no key needed).
    """
    # Geocode first — always needed
    lat, lng = await _geocode(city)

    api_key = settings.google_places_api_key
    if not api_key or api_key == "your_key_here":
        # ── OpenStreetMap path ────────────────────────────────────────────
        companies = await _overpass_search(lat, lng, entity_type)
        return companies, lat, lng

    # ── Google Places path ───────────────────────────────────────────────
    location_term = f"pincode {city}" if _is_pincode(city) else city
    base_query    = _ENTITY_QUERIES.get(entity_type, _ENTITY_QUERIES["All"])
    hint          = _INSTRUMENT_HINT.get(instrument_type, "")
    query         = f"{base_query} {hint} in {location_term} India".strip()
    entity_label  = _ENTITY_LABEL.get(entity_type, "Financial Entity")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{PLACES_BASE}/textsearch/json",
                params={"query": query, "key": api_key,
                        "type": "establishment", "region": "in"},
            )
            data = resp.json()

        results = data.get("results", [])[:10]
        if not results:
            # Fallback to Overpass if Places returns nothing
            companies = await _overpass_search(lat, lng, entity_type)
            return companies, lat, lng

        companies = []
        for idx, place in enumerate(results):
            loc = place.get("geometry", {}).get("location", {})
            companies.append({
                "id":          place.get("place_id", f"place_{idx}"),
                "name":        place.get("name", "Unknown"),
                "address":     place.get("formatted_address", ""),
                "lat":         loc.get("lat", lat),
                "lng":         loc.get("lng", lng),
                "website":     "",
                "phone":       "",
                "entity_type": entity_label,
            })

        # Enrich with website / phone
        async with httpx.AsyncClient(timeout=20) as client:
            for c in companies:
                try:
                    det = await client.get(
                        f"{PLACES_BASE}/details/json",
                        params={"place_id": c["id"],
                                "fields": "website,formatted_phone_number",
                                "key": api_key},
                    )
                    detail = det.json().get("result", {})
                    c["website"] = detail.get("website", "")
                    c["phone"]   = detail.get("formatted_phone_number", "")
                except Exception:
                    pass

        return companies, lat, lng

    except Exception:
        companies = await _overpass_search(lat, lng, entity_type)
        return companies, lat, lng
