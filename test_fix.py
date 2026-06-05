import asyncio, httpx

HEADERS = {"User-Agent": "CredSight/2.0 test"}
NOMINATIM = "https://nominatim.openstreetmap.org/search"

async def nominatim_search(query, lat, lng, limit=10):
    """Search Nominatim for businesses by name near a location."""
    async with httpx.AsyncClient(timeout=10, headers=HEADERS) as c:
        r = await c.get(NOMINATIM, params={
            "q": query,
            "format": "json",
            "limit": limit,
            "countrycodes": "in",
            "viewbox": f"{lng-0.3},{lat+0.3},{lng+0.3},{lat-0.3}",
            "bounded": 1,
            "addressdetails": 1,
        })
        return r.json()

async def test():
    # Geocode Mumbai
    async with httpx.AsyncClient(timeout=8, headers=HEADERS) as c:
        r = await c.get(NOMINATIM, params={
            "q": "Mumbai, Maharashtra, India", "format": "json", "limit": 1, "countrycodes": "in"
        })
        geo = r.json()
        lat, lng = float(geo[0]["lat"]), float(geo[0]["lon"])
        print(f"Mumbai: {lat}, {lng}\n")

    # Test different search strategies
    searches = [
        ("Banks via Nominatim", "bank Mumbai"),
        ("NBFCs via Nominatim", "finance company Mumbai"),
        ("Corporates via Nominatim", "corporate office Mumbai"),
        ("HDFC specific", "HDFC Mumbai"),
        ("SBI specific", "State Bank of India Mumbai"),
        ("Bajaj Finance", "Bajaj Finance Mumbai"),
    ]

    for label, query in searches:
        results = await nominatim_search(query, lat, lng, limit=5)
        print(f"{label}: {len(results)} results")
        for r in results[:5]:
            name = r.get("display_name", "").split(",")[0]
            rlat = r.get("lat", "")
            rlon = r.get("lon", "")
            print(f"  - {name}  ({rlat}, {rlon})")
        print()
        await asyncio.sleep(1.1)  # Nominatim rate limit

asyncio.run(test())
