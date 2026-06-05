import httpx
from backend.config import settings

PLACES_BASE = "https://maps.googleapis.com/maps/api/place"
NOMINATIM   = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_HEADERS = {"User-Agent": "LeadRadar/2.0 (credit-rating-lead-tool)"}


async def _geocode(location: str) -> tuple[float, float]:
    """Geocode a city/pincode in India using Nominatim (free, no key needed)."""
    try:
        async with httpx.AsyncClient(timeout=6, headers=_NOMINATIM_HEADERS) as client:
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
    return 20.5937, 78.9629  # India centre fallback

_ENTITY_QUERIES: dict[str, list[str]] = {
    "Banks": [
        "private sector bank",
        "public sector bank India",
        "scheduled commercial bank",
    ],
    "NBFCs": [
        "NBFC non banking financial company",
        "housing finance company",
        "micro finance institution",
    ],
    "Corporates": [
        "corporate company head office",
        "listed company India",
        "infrastructure company",
        "manufacturing company corporate",
    ],
    "All": [
        "bank NBFC financial company corporate",
    ],
}

_INSTRUMENT_HINT: dict[str, str] = {
    "NCD": "NCD debentures issuer",
    "Bond": "bond issuer",
    "IPO": "IPO listed company",
    "Debt": "debt financing borrower",
    "All": "",
}

_ENTITY_LABEL: dict[str, str] = {
    "Banks": "Bank",
    "NBFCs": "NBFC",
    "Corporates": "Corporate",
    "All": "Financial Entity",
}


def _build_query(city: str, entity_type: str, instrument_type: str) -> str:
    base = _ENTITY_QUERIES.get(entity_type, _ENTITY_QUERIES["All"])[0]
    hint = _INSTRUMENT_HINT.get(instrument_type, "")
    if hint:
        return f"{base} {hint} in {city} India"
    return f"{base} in {city} India"


def _is_pincode(location: str) -> bool:
    return location.strip().isdigit() and len(location.strip()) == 6


async def discover_companies(
    city: str,
    industry: str = "",
    entity_type: str = "All",
    instrument_type: str = "All",
) -> tuple[list[dict], float, float]:
    """
    Search Google Places for companies matching entity_type + instrument_type in city/pincode.
    Returns (companies, city_lat, city_lng).
    """
    api_key = settings.google_places_api_key
    if not api_key or api_key == "your_key_here":
        return await _mock_companies(city, entity_type)

    location_term = city
    if _is_pincode(city):
        location_term = f"pincode {city}"

    query = _build_query(location_term, entity_type, instrument_type)
    entity_label = _ENTITY_LABEL.get(entity_type, "Financial Entity")

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{PLACES_BASE}/textsearch/json",
            params={
                "query": query,
                "key": api_key,
                "type": "establishment",
                "region": "in",
            },
        )
        data = resp.json()

    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        return [], 0.0, 0.0

    results = data.get("results", [])[:10]
    if not results:
        return [], 0.0, 0.0

    companies = []
    for idx, place in enumerate(results):
        loc = place.get("geometry", {}).get("location", {})
        companies.append({
            "id": place.get("place_id", f"place_{idx}"),
            "name": place.get("name", "Unknown"),
            "address": place.get("formatted_address", ""),
            "lat": loc.get("lat", 0.0),
            "lng": loc.get("lng", 0.0),
            "website": "",
            "phone": "",
            "entity_type": entity_label,
        })

    enriched = []
    async with httpx.AsyncClient(timeout=20) as client:
        for c in companies:
            try:
                det = await client.get(
                    f"{PLACES_BASE}/details/json",
                    params={
                        "place_id": c["id"],
                        "fields": "website,formatted_phone_number",
                        "key": api_key,
                    },
                )
                detail = det.json().get("result", {})
                c["website"] = detail.get("website", "")
                c["phone"] = detail.get("formatted_phone_number", "")
            except Exception:
                pass
            enriched.append(c)

    city_lat = enriched[0]["lat"] if enriched else 20.5937
    city_lng = enriched[0]["lng"] if enriched else 78.9629
    return enriched, city_lat, city_lng


async def _mock_companies(city: str, entity_type: str = "All") -> tuple[list[dict], float, float]:
    """Return mock data — geocodes the city via Nominatim so map zooms correctly."""
    base_lat, base_lng = await _geocode(city)
    entity_label = _ENTITY_LABEL.get(entity_type, "Financial Entity")

    bank_names = [
        "Axis Bank Ltd", "HDFC Bank — Nariman Point", "ICICI Bank Corporate Office",
        "Kotak Mahindra Bank HQ", "Yes Bank Limited",
    ]
    nbfc_names = [
        "Bajaj Finance Ltd", "Muthoot Finance Ltd", "Mahindra Finance HQ",
        "Shriram Transport Finance", "Cholamandalam Investment",
    ]
    corp_names = [
        "Tata Capital Financial Services", "L&T Finance Holdings",
        "Reliance Capital Ltd", "Aditya Birla Finance", "Hero FinCorp Ltd",
    ]

    name_pool = {
        "Banks": bank_names,
        "NBFCs": nbfc_names,
        "Corporates": corp_names,
        "All": bank_names[:2] + nbfc_names[:2] + corp_names[:2],
    }.get(entity_type, bank_names)

    companies = []
    for i, name in enumerate(name_pool[:10]):
        companies.append({
            "id": f"mock_{i}",
            "name": name,
            "address": f"{i + 1} Financial District, {city}, India",
            "lat": base_lat + (i * 0.025 - 0.1),
            "lng": base_lng + (i * 0.025 - 0.1),
            "website": f"www.{name.lower().split()[0]}.com",
            "phone": f"+91 22 {4000 + i * 111:04d} 0000",
            "entity_type": entity_label,
        })
    return companies, base_lat, base_lng
