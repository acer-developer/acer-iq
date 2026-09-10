"""
NSE sector indices - live percent change so we know which sectors are
bullish vs bearish today.

Uses the same NSE cookie-warmup pattern as market_news.py.
"""

import time

import httpx

NSE_INDICES = "https://www.nseindia.com/api/allIndices"
NSE_WARMUP = "https://www.nseindia.com"

_WARMUP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/live-market-indices",
    "Connection": "keep-alive",
}

# Sector indices only (exclude broad market indices like Nifty 50)
_SECTOR_KEYWORDS = [
    "NIFTY BANK", "NIFTY AUTO", "NIFTY IT", "NIFTY PHARMA", "NIFTY FMCG",
    "NIFTY METAL", "NIFTY REALTY", "NIFTY ENERGY", "NIFTY FIN SERVICE",
    "NIFTY PSU BANK", "NIFTY PVT BANK", "NIFTY MEDIA", "NIFTY HEALTHCARE",
    "NIFTY CONSUMER DURABLES", "NIFTY OIL", "NIFTY COMMODITIES",
    "NIFTY INFRA", "NIFTY MNC", "NIFTY SERVICES", "NIFTY IND DIGITAL",
    "NIFTY CPSE", "NIFTY PSE", "NIFTY MID", "NIFTY SMALLCAP",
]

# Map NSE index name to a "sector" that maps intuitively to signals
_SECTOR_LINKS = {
    "NIFTY BANK": "https://www.nseindia.com/market-data/live-equity-market?symbol=NIFTY%20BANK",
    "NIFTY AUTO": "https://www.nseindia.com/market-data/live-equity-market?symbol=NIFTY%20AUTO",
    "NIFTY IT": "https://www.nseindia.com/market-data/live-equity-market?symbol=NIFTY%20IT",
    "NIFTY PHARMA": "https://www.nseindia.com/market-data/live-equity-market?symbol=NIFTY%20PHARMA",
    "NIFTY FMCG": "https://www.nseindia.com/market-data/live-equity-market?symbol=NIFTY%20FMCG",
    "NIFTY METAL": "https://www.nseindia.com/market-data/live-equity-market?symbol=NIFTY%20METAL",
    "NIFTY REALTY": "https://www.nseindia.com/market-data/live-equity-market?symbol=NIFTY%20REALTY",
    "NIFTY ENERGY": "https://www.nseindia.com/market-data/live-equity-market?symbol=NIFTY%20ENERGY",
}

_client: httpx.AsyncClient | None = None
_warmed_at = 0.0


def _get_client(force_new: bool = False) -> httpx.AsyncClient:
    global _client
    if force_new and _client is not None and not _client.is_closed:
        try:
            import asyncio
            asyncio.get_event_loop().create_task(_client.aclose())
        except Exception:
            pass
        _client = None
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True)
    return _client


async def _warmup(client: httpx.AsyncClient) -> None:
    global _warmed_at
    if time.time() - _warmed_at > 120:
        try:
            r = await client.get(NSE_WARMUP, headers=_WARMUP_HEADERS)
            if r.status_code in (200, 403) and client.cookies:
                _warmed_at = time.time()
        except Exception:
            pass


def _is_sector(name: str) -> bool:
    n = name.upper()
    for kw in _SECTOR_KEYWORDS:
        if kw in n:
            return True
    return False


def _sector_link(name: str) -> str:
    u = name.upper()
    for key, link in _SECTOR_LINKS.items():
        if key in u:
            return link
    return "https://www.nseindia.com/market-data/live-market-indices"


async def _try_fetch(client: httpx.AsyncClient):
    await _warmup(client)
    r = await client.get(NSE_INDICES)
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return None, f"NSE returned HTTP {r.status_code}"
    return r.json(), None


async def fetch_sector_indices() -> dict:
    client = _get_client()
    data = None
    err = ""
    try:
        data, err = await _try_fetch(client)
    except Exception as e:
        err = str(e) or "Connection failed"

    if data is None:
        global _warmed_at
        _warmed_at = 0.0
        client = _get_client(force_new=True)
        try:
            data, err = await _try_fetch(client)
        except Exception as e:
            data = None
            err = str(e) or "Connection failed"

    if data is None:
        return {
            "sectors": [], "bullish": [], "bearish": [],
            "status": "blocked", "message": f"NSE unreachable. {err}",
        }

    raw = data.get("data", []) if isinstance(data, dict) else data
    sectors = []
    for it in raw:
        name = str(it.get("index") or it.get("indexSymbol") or "").strip()
        if not _is_sector(name):
            continue
        try:
            pct = float(it.get("percentChange") or it.get("perChange") or 0)
        except (TypeError, ValueError):
            pct = 0.0
        try:
            last = float(it.get("last") or it.get("lastPrice") or 0)
        except (TypeError, ValueError):
            last = 0.0
        try:
            prev = float(it.get("previousClose") or 0)
        except (TypeError, ValueError):
            prev = 0.0

        sectors.append({
            "name": name,
            "short_name": name.replace("NIFTY ", "").replace("Nifty ", "").title(),
            "pct_change": round(pct, 2),
            "last": round(last, 2),
            "prev_close": round(prev, 2),
            "link": _sector_link(name),
        })

    sectors.sort(key=lambda x: x["pct_change"], reverse=True)
    bullish = [s for s in sectors if s["pct_change"] > 0][:8]
    bearish = [s for s in sectors if s["pct_change"] < 0][::-1][:8]

    return {
        "sectors": sectors,
        "bullish": bullish,
        "bearish": bearish,
        "status": "ok",
        "source": "NSE Live Market Indices",
        "source_link": "https://www.nseindia.com/market-data/live-market-indices",
        "total": len(sectors),
    }
