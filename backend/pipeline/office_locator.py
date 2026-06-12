import logging

import httpx

from backend.config import settings
from backend.netutil import SourceStatus, cache, limiter

log = logging.getLogger("acer_iq.offices")

PLACES_BASE = "https://maps.googleapis.com/maps/api/place"
CACHE_TTL = 6 * 3600


async def find_office_locations(company_name: str, hq_lat: float, hq_lng: float,
                                status: SourceStatus | None = None) -> list[dict]:
    """Find branch/office locations of a company across India via Google Places."""
    api_key = settings.google_places_api_key
    if not api_key or api_key == "your_key_here":
        if status:
            status.skip("google_places", "no API key configured")
        return []

    key = "offices:" + company_name.lower()
    hit = cache.get(key)
    if hit is not None:
        if status:
            status.ok("google_places")
        return hit

    offices = []
    seen_ids: set[str] = set()

    async with limiter("places"):
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{PLACES_BASE}/textsearch/json",
                params={
                    "query": f"{company_name} office branch India",
                    "key": api_key,
                    "type": "establishment",
                },
            )
        if resp.status_code != 200:
            log.warning("Places office search HTTP %s for %r", resp.status_code, company_name)
            if status:
                status.fail("google_places", f"HTTP {resp.status_code}")
            return []
        if status:
            status.ok("google_places")

        data = resp.json()
        for place in data.get("results", [])[:20]:
            pid = place.get("place_id", "")
            if pid in seen_ids:
                continue
            seen_ids.add(pid)

            loc = place.get("geometry", {}).get("location", {})
            lat = loc.get("lat", 0.0)
            lng = loc.get("lng", 0.0)

            # Classify as HQ if very close to the known headquarters
            if abs(lat - hq_lat) < 0.002 and abs(lng - hq_lng) < 0.002:
                loc_type = "HQ"
            elif "head office" in place.get("name", "").lower() or "hq" in place.get("name", "").lower():
                loc_type = "Head Office"
            elif "regional" in place.get("name", "").lower():
                loc_type = "Regional Office"
            else:
                loc_type = "Branch"

            offices.append({
                "place_id": pid,
                "name": place.get("name", company_name),
                "address": place.get("formatted_address", ""),
                "lat": lat,
                "lng": lng,
                "location_type": loc_type,
            })

    cache.set_result(key, offices, CACHE_TTL)
    return offices
