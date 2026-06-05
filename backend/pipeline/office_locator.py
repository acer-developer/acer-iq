import httpx
from backend.config import settings

PLACES_BASE = "https://maps.googleapis.com/maps/api/place"


async def find_office_locations(company_name: str, hq_lat: float, hq_lng: float) -> list[dict]:
    """Find branch/office locations of a company across India via Google Places."""
    api_key = settings.google_places_api_key
    if not api_key or api_key == "your_key_here":
        return []

    offices = []
    seen_ids: set[str] = set()

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
            return []

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

    return offices
