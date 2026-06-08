import httpx
from backend.config import settings

PLACES_BASE  = "https://maps.googleapis.com/maps/api/place"
NOMINATIM    = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

_HEADERS = {"User-Agent": "AcerIQ/1.0 (credit-rating-lead-tool; contact@acerratings.com)"}

_ENTITY_LABEL = {
    "Banks": "Bank", "NBFCs": "NBFC", "Corporates": "Corporate", "All": "Financial Entity",
}

# ── Hardcoded coords for major Indian cities ──────────────────────────────────
_CITY_COORDS: dict[str, tuple[float, float]] = {
    "mumbai": (19.0760, 72.8777), "delhi": (28.6139, 77.2090),
    "new delhi": (28.6139, 77.2090), "bengaluru": (12.9716, 77.5946),
    "bangalore": (12.9716, 77.5946), "hyderabad": (17.3850, 78.4867),
    "chennai": (13.0827, 80.2707), "kolkata": (22.5726, 88.3639),
    "pune": (18.5204, 73.8567), "ahmedabad": (23.0225, 72.5714),
    "jaipur": (26.9124, 75.7873), "lucknow": (26.8467, 80.9462),
    "kanpur": (26.4499, 80.3319), "nagpur": (21.1458, 79.0882),
    "visakhapatnam": (17.6868, 83.2185), "vizag": (17.6868, 83.2185),
    "indore": (22.7196, 75.8577), "thane": (19.2183, 72.9781),
    "bhopal": (23.2599, 77.4126), "patna": (25.6093, 85.1236),
    "vadodara": (22.3072, 73.1812), "ghaziabad": (28.6692, 77.4538),
    "ludhiana": (30.9010, 75.8573), "agra": (27.1767, 78.0081),
    "nashik": (19.9975, 73.7898), "varanasi": (25.3176, 82.9739),
    "meerut": (28.9845, 77.7064), "rajkot": (22.3039, 70.8022),
    "srinagar": (34.0837, 74.7973), "aurangabad": (19.8762, 75.3433),
    "dhanbad": (23.7957, 86.4304), "amritsar": (31.6340, 74.8723),
    "navi mumbai": (19.0330, 73.0297), "allahabad": (25.4358, 81.8463),
    "prayagraj": (25.4358, 81.8463), "howrah": (22.5958, 88.2636),
    "ranchi": (23.3441, 85.3096), "gwalior": (26.2183, 78.1828),
    "jabalpur": (23.1815, 79.9864), "coimbatore": (11.0168, 76.9558),
    "vijayawada": (16.5062, 80.6480), "jodhpur": (26.2389, 73.0243),
    "madurai": (9.9252, 78.1198), "raipur": (21.2514, 81.6296),
    "kota": (25.2138, 75.8648), "chandigarh": (30.7333, 76.7794),
    "guwahati": (26.1445, 91.7362), "solapur": (17.6599, 75.9064),
    "hubli": (15.3647, 75.1240), "hubballi": (15.3647, 75.1240),
    "mysuru": (12.2958, 76.6394), "mysore": (12.2958, 76.6394),
    "tiruchirappalli": (10.7905, 78.7047), "trichy": (10.7905, 78.7047),
    "bareilly": (28.3670, 79.4304), "aligarh": (27.8974, 78.0880),
    "moradabad": (28.8389, 78.7768), "gorakhpur": (26.7606, 83.3732),
    "bikaner": (28.0229, 73.3119), "amravati": (20.9374, 77.7796),
    "noida": (28.5355, 77.3910), "jamshedpur": (22.8046, 86.2029),
    "bhilai": (21.2094, 81.3784), "cuttack": (20.4625, 85.8830),
    "kochi": (9.9312, 76.2673), "nellore": (14.4426, 79.9865),
    "bhavnagar": (21.7645, 72.1519), "dehradun": (30.3165, 78.0322),
    "durgapur": (23.5204, 87.3119), "asansol": (23.6739, 86.9524),
    "rourkela": (22.2604, 84.8536), "nanded": (19.1383, 77.3210),
    "kolhapur": (16.7050, 74.2433), "ajmer": (26.4499, 74.6399),
    "gulbarga": (17.3297, 76.8343), "latur": (18.4088, 76.5604),
    "mangaluru": (12.9141, 74.8560), "mangalore": (12.9141, 74.8560),
    "erode": (11.3410, 77.7172), "tiruppur": (11.1085, 77.3411),
    "shimla": (31.1048, 77.1734), "gangtok": (27.3389, 88.6065),
    "panaji": (15.4909, 73.8278), "goa": (15.4909, 73.8278),
    "imphal": (24.8170, 93.9368), "shillong": (25.5788, 91.8933),
    "puducherry": (11.9416, 79.8083), "pondicherry": (11.9416, 79.8083),
    "surat": (21.1702, 72.8311), "gandhinagar": (23.2156, 72.6369),
    "thiruvananthapuram": (8.5241, 76.9366), "kozhikode": (11.2588, 75.7804),
    "thrissur": (10.5276, 76.2144), "salem": (11.6643, 78.1460),
    "tirunelveli": (8.7139, 77.7567), "vellore": (12.9165, 79.1325),
    "warangal": (17.9784, 79.5941), "guntur": (16.3067, 80.4365),
    "udaipur": (24.5854, 73.7125), "bhubaneswar": (20.2961, 85.8245),
    "siliguri": (26.7271, 88.3953), "jammu": (32.7266, 74.8570),
    "rohtak": (28.8955, 76.6066), "panipat": (29.3909, 76.9635),
    "mathura": (27.4924, 77.6737), "bilaspur": (22.0797, 82.1409),
    "sangli": (16.8524, 74.5815), "ujjain": (23.1765, 75.7885),
    "secunderabad": (17.4399, 78.4983), "bellary": (15.1394, 76.9214),
    "faridabad": (28.4089, 77.3178), "gurugram": (28.4595, 77.0266),
    "gurgaon": (28.4595, 77.0266),
}


def _parse_city(location: str) -> str:
    return location.split(",")[0].strip()


# ── Geocoding ─────────────────────────────────────────────────────────────────

async def _geocode(location: str) -> tuple[float, float]:
    city = _parse_city(location).lower()
    if city in _CITY_COORDS:
        return _CITY_COORDS[city]

    try:
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
            resp = await client.get(NOMINATIM, params={
                "q": f"{location}, India", "format": "json",
                "limit": 1, "countrycodes": "in",
            })
            results = resp.json()
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        pass

    raw = location.strip()
    if raw.isdigit() and len(raw) == 6:
        try:
            async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
                resp = await client.get(NOMINATIM, params={
                    "postalcode": raw, "country": "India",
                    "format": "json", "limit": 1,
                })
                results = resp.json()
                if results:
                    return float(results[0]["lat"]), float(results[0]["lon"])
        except Exception:
            pass

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
        return f"""[out:json][timeout:30];
(
  node["amenity"="bank"](around:{radius},{lat},{lng});
  way["amenity"="bank"](around:{radius},{lat},{lng});
  node["name"~"Gramin Bank|Sahakari Bank|Co-operative Bank|Urban Bank|Rural Bank",i]["name"](around:{radius},{lat},{lng});
  way["name"~"Gramin Bank|Sahakari Bank|Co-operative Bank|Urban Bank|Rural Bank",i]["name"](around:{radius},{lat},{lng});
);
out center 60;"""

    elif entity_type == "NBFCs":
        return f"""[out:json][timeout:30];
(
  node["office"~"financial|insurance|moneylender"](around:{radius},{lat},{lng});
  way["office"~"financial|insurance|moneylender"](around:{radius},{lat},{lng});
  node["name"~"Finance|Capital|NBFC|Housing Finance|Microfinance|Micro Finance|Leasing|Fincorp|Finserv|Securities|Asset Management|Investment Fund",i]["name"](around:{radius},{lat},{lng});
  way["name"~"Finance|Capital|NBFC|Housing Finance|Microfinance|Micro Finance|Leasing|Fincorp|Finserv|Securities|Asset Management|Investment Fund",i]["name"](around:{radius},{lat},{lng});
);
out center 60;"""

    elif entity_type == "Corporates":
        return f"""[out:json][timeout:30];
(
  node["office"~"company|government"](around:{radius},{lat},{lng});
  way["office"~"company|government"](around:{radius},{lat},{lng});
  node["name"~"Limited|Industries|Infrastructure|Corporation|Holdings|Enterprises|Technologies|Power|Steel|Cement|Pharma|Energy|Chemicals|Textiles|Constructions",i]["name"](around:{radius},{lat},{lng});
  way["name"~"Limited|Industries|Infrastructure|Corporation|Holdings|Enterprises|Technologies|Power|Steel|Cement|Pharma|Energy|Chemicals|Textiles|Constructions",i]["name"](around:{radius},{lat},{lng});
);
out center 60;"""

    else:  # All — balanced mix, 20 each type
        return f"""[out:json][timeout:30];
(
  node["amenity"="bank"](around:{radius},{lat},{lng});
  way["amenity"="bank"](around:{radius},{lat},{lng});
  node["office"~"financial|insurance|company"](around:{radius},{lat},{lng});
  way["office"~"financial|insurance|company"](around:{radius},{lat},{lng});
  node["name"~"Finance|Capital|NBFC|Housing|Industries|Infrastructure|Corporation|Holdings|Technologies",i]["name"](around:{radius},{lat},{lng});
  way["name"~"Finance|Capital|NBFC|Housing|Industries|Infrastructure|Corporation|Holdings|Technologies",i]["name"](around:{radius},{lat},{lng});
);
out center 60;"""


# ── Overpass search ───────────────────────────────────────────────────────────

async def _overpass_search(lat: float, lng: float, entity_type: str,
                           radius: int = 15000) -> list[dict]:
    entity_label = _ENTITY_LABEL.get(entity_type, "Financial Entity")
    q = _build_overpass_query(lat, lng, entity_type, radius)

    try:
        async with httpx.AsyncClient(timeout=35, headers=_HEADERS) as client:
            resp = await client.post(OVERPASS_URL, data={"data": q})
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

        # Skip ATMs and kiosks — not company headquarters
        name_lower = name.lower()
        if any(skip in name_lower for skip in ("atm", "kiosk", "extension counter")):
            continue

        entity = _classify(tags, entity_label)

        # Strict entity filter per mode
        if entity_type == "Banks" and entity != "Bank":
            continue
        if entity_type == "NBFCs" and entity != "NBFC":
            continue
        if entity_type == "Corporates" and entity != "Corporate":
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

    return companies


# ── Public entry point ────────────────────────────────────────────────────────

def _is_pincode(s: str) -> bool:
    return s.strip().isdigit() and len(s.strip()) == 6


async def discover_companies(
    city: str,
    industry: str = "",
    entity_type: str = "All",
    instrument_type: str = "All",
) -> tuple[list[dict], float, float]:
    lat, lng = await _geocode(city)

    companies = await _overpass_search(lat, lng, entity_type, radius=15000)

    # Widen search if fewer than 8 results found
    if len(companies) < 8:
        wider = await _overpass_search(lat, lng, entity_type, radius=30000)
        seen_names = {c["name"].lower() for c in companies}
        for c in wider:
            if c["name"].lower() not in seen_names:
                companies.append(c)
                seen_names.add(c["name"].lower())

    # Google Places fallback if still empty
    if not companies:
        api_key = settings.google_places_api_key
        if api_key and api_key not in ("your_key_here", ""):
            companies = await _google_places_search(city, entity_type, api_key, lat, lng)

    return companies[:30], lat, lng


async def _google_places_search(city, entity_type, api_key, lat, lng):
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
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{PLACES_BASE}/textsearch/json",
                params={"query": query, "key": api_key, "type": "establishment", "region": "in"},
            )
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
    except Exception:
        pass
    return companies
