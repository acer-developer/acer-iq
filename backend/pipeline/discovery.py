import httpx
from backend.config import settings

PLACES_BASE   = "https://maps.googleapis.com/maps/api/place"
NOMINATIM     = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL  = "https://overpass-api.de/api/interpreter"

_HEADERS = {"User-Agent": "CredSight/2.0 (credit-rating-lead-tool; contact@acerratings.com)"}

_ENTITY_LABEL: dict[str, str] = {
    "Banks":      "Bank",
    "NBFCs":      "NBFC",
    "Corporates": "Corporate",
    "All":        "Financial Entity",
}

# ── Geocoding ────────────────────────────────────────────────────────────────

async def _geocode(location: str) -> tuple[float, float]:
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
    return 20.5937, 78.9629


# ── Overpass (real OSM data) ─────────────────────────────────────────────────

# For ALL entity types, we search for financial institutions broadly.
# OSM tags banks well. For NBFCs/Corporates, we search banks + offices + known name patterns.
_OVERPASS_QUERIES = {
    "Banks": """
  node["amenity"="bank"](around:{r},{lat},{lng});
  way["amenity"="bank"](around:{r},{lat},{lng});
""",
    "NBFCs": """
  node["amenity"="bank"](around:{r},{lat},{lng});
  way["amenity"="bank"](around:{r},{lat},{lng});
  node["office"~"financial|insurance"](around:{r},{lat},{lng});
  way["office"~"financial|insurance"](around:{r},{lat},{lng});
  node["name"~"finance|NBFC|capital|housing|micro|leasing|credit",i](around:{r},{lat},{lng});
  way["name"~"finance|NBFC|capital|housing|micro|leasing|credit",i](around:{r},{lat},{lng});
""",
    "Corporates": """
  node["amenity"="bank"](around:{r},{lat},{lng});
  way["amenity"="bank"](around:{r},{lat},{lng});
  node["office"~"financial|company|commercial|insurance"](around:{r},{lat},{lng});
  way["office"~"financial|company|commercial|insurance"](around:{r},{lat},{lng});
  node["name"~"ltd|limited|pvt|private|corporation|holdings|group",i]["name"](around:{r},{lat},{lng});
  way["name"~"ltd|limited|pvt|private|corporation|holdings|group",i]["name"](around:{r},{lat},{lng});
""",
    "All": """
  node["amenity"="bank"](around:{r},{lat},{lng});
  way["amenity"="bank"](around:{r},{lat},{lng});
  node["office"~"financial|company|commercial|insurance"](around:{r},{lat},{lng});
  way["office"~"financial|company|commercial|insurance"](around:{r},{lat},{lng});
""",
}


def _entity_from_tags(tags: dict, fallback: str) -> str:
    amenity = tags.get("amenity", "")
    office  = tags.get("office", "")
    name    = tags.get("name", "").lower()

    if amenity == "bank":
        return "Bank"
    if office in ("financial", "insurance", "moneylender"):
        return "NBFC"
    if any(w in name for w in ("finance", "capital", "nbfc", "housing finance",
                                "micro", "leasing", "credit")):
        return "NBFC"
    if office in ("company", "commercial"):
        return "Corporate"
    if any(w in name for w in ("ltd", "limited", "pvt", "corporation", "holdings")):
        return "Corporate"
    return fallback


def _address_from_tags(tags: dict) -> str:
    parts = []
    for key in ("addr:housename", "addr:housenumber", "addr:street",
                "addr:suburb", "addr:city", "addr:state"):
        v = tags.get(key, "").strip()
        if v:
            parts.append(v)
    return ", ".join(parts)


async def _overpass_search(lat: float, lng: float,
                           entity_type: str, radius_m: int = 15000) -> list[dict]:
    """Query Overpass API for real financial entities near a point."""
    entity_label = _ENTITY_LABEL.get(entity_type, "Financial Entity")
    body = _OVERPASS_QUERIES.get(entity_type, _OVERPASS_QUERIES["All"])
    body = body.format(r=radius_m, lat=lat, lng=lng)

    query = f"[out:json][timeout:25];\n(\n{body}\n);\nout center 25;"

    try:
        async with httpx.AsyncClient(timeout=25, headers=_HEADERS) as client:
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
            "address":     _address_from_tags(tags),
            "lat":         clat,
            "lng":         clng,
            "website":     tags.get("website", tags.get("url", "")),
            "phone":       tags.get("phone", tags.get("contact:phone", "")),
            "entity_type": _entity_from_tags(tags, entity_label),
        })

        if len(companies) >= 15:
            break

    return companies


# ── Google Places path ───────────────────────────────────────────────────────

_GPLACES_QUERIES = {
    "Banks":      "bank scheduled commercial bank",
    "NBFCs":      "NBFC non banking financial company housing finance",
    "Corporates": "corporate company head office listed company",
    "All":        "bank NBFC financial company corporate",
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
    Primary: Overpass API (real OSM data).
    Fallback: Google Places if API key is set.
    NEVER returns fake/mock data.
    """
    lat, lng = await _geocode(city)

    # Try Overpass first (free, real data)
    companies = await _overpass_search(lat, lng, entity_type)

    # If Overpass returned results, we're done
    if companies:
        return companies, lat, lng

    # Try wider radius
    companies = await _overpass_search(lat, lng, entity_type, radius_m=30000)
    if companies:
        return companies, lat, lng

    # Google Places fallback (if key is configured)
    api_key = settings.google_places_api_key
    if api_key and api_key != "your_key_here":
        location_term = f"pincode {city}" if _is_pincode(city) else city
        base_query    = _GPLACES_QUERIES.get(entity_type, _GPLACES_QUERIES["All"])
        query         = f"{base_query} in {location_term} India"
        entity_label  = _ENTITY_LABEL.get(entity_type, "Financial Entity")

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{PLACES_BASE}/textsearch/json",
                    params={"query": query, "key": api_key,
                            "type": "establishment", "region": "in"},
                )
                results = resp.json().get("results", [])[:10]
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
        except Exception:
            pass

    # Return whatever we found (may be empty — frontend handles gracefully)
    return companies, lat, lng
