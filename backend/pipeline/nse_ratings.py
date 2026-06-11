"""
Credit rating actions from NSE corporate disclosures.

Under SEBI LODR, listed companies (and debt-listed issuers) must disclose
every credit rating action to the exchange. NSE exposes these at
/api/corporate-credit-rating — agency, rating, action (Upgrade/Downgrade/
Withdrawn/...), and date. This is the authoritative replacement for the
thin RATING_AGENCY fields on BSE's debt-search API.

Caveat: the `issuer` parameter is an EXACT name match against NSE's
records, so we try several spelling variants and report honestly when
nothing matches.
"""

import re
import time

import httpx

NSE_API = "https://www.nseindia.com/api/corporate-credit-rating"
NSE_WARMUP = "https://www.nseindia.com"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-credit-rating",
}

_client: httpx.AsyncClient | None = None
_warmed_at = 0.0

_breaker = {"fails": 0, "until": 0.0}

_cache: dict[str, list] = {}


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True)
    return _client


def _tripped() -> bool:
    return time.time() < _breaker["until"]


def _record(ok: bool) -> None:
    if ok:
        _breaker["fails"] = 0
    else:
        _breaker["fails"] += 1
        if _breaker["fails"] >= 4:
            _breaker["until"] = time.time() + 600
            _breaker["fails"] = 0


async def _warmup(client: httpx.AsyncClient) -> None:
    """NSE requires cookies from the main site; refresh every 5 minutes."""
    global _warmed_at
    if time.time() - _warmed_at > 300:
        await client.get(NSE_WARMUP, headers={**_HEADERS, "Accept": "text/html,*/*"})
        _warmed_at = time.time()


def _name_variants(name: str) -> list[str]:
    """NSE issuer match is exact — generate likely spellings."""
    n = " ".join(name.split()).strip()
    # Drop "(Formerly: ...)" suffixes from RBI registry names
    n = re.sub(r"\s*\((Formerly|Earlier)[^)]*\)\s*", " ", n, flags=re.I).strip()
    n = n.rstrip(".,")
    variants = [n]
    if re.search(r"\bLtd\.?$", n, re.I):
        variants.append(re.sub(r"\bLtd\.?$", "Limited", n, flags=re.I))
    if re.search(r"\bLimited$", n, re.I):
        variants.append(re.sub(r"\bLimited$", "Ltd", n, flags=re.I))
    if re.search(r"\bPvt\.?\b", n, re.I):
        variants.append(re.sub(r"\bPvt\.?\b", "Private", n, flags=re.I))
    if re.search(r"\bPrivate\b", n, re.I):
        variants.append(re.sub(r"\bPrivate\b", "Pvt", n, flags=re.I))
    # dedupe, preserve order
    seen, out = set(), []
    for v in variants:
        if v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out[:5]


def _normalize(item: dict) -> dict:
    return {
        "agency": (item.get("NameOfCRAgency") or "").strip(),
        "rating": (item.get("CreditRating") or "").strip(),
        "action": (item.get("RatingAction") or "").strip(),
        "date": (item.get("DateofCR") or "").strip(),       # DD-MM-YYYY
        "isin": (item.get("ISIN") or "").replace("ZZZ999Z99999", "").strip(),
        "company_name": (item.get("CompanyName") or "").strip(),
    }


def _date_key(d: str) -> tuple:
    """DD-MM-YYYY → sortable (yyyy, mm, dd)."""
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", d or "")
    return (m.group(3), m.group(2), m.group(1)) if m else ("0", "0", "0")


async def fetch_rating_actions(company_name: str) -> tuple[list[dict], str]:
    """
    Returns (actions, status).
    status: "ok"        — NSE answered, actions found
            "none"      — NSE answered, no disclosures matched this name
            "blocked"   — NSE unreachable / blocking us (data NOT verified)
    Actions sorted newest first.
    """
    key = company_name.strip().lower()
    if key in _cache:
        return _cache[key], "ok" if _cache[key] else "none"
    if _tripped():
        return [], "blocked"

    client = _get_client()
    try:
        await _warmup(client)
    except Exception:
        pass

    actions: list[dict] = []
    answered = False
    try:
        for variant in _name_variants(company_name):
            for index in ("debt", "equities"):
                r = await client.get(NSE_API, params={"index": index, "issuer": variant})
                if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
                    _record(False)
                    continue
                _record(True)
                answered = True
                data = r.json()
                items = data if isinstance(data, list) else data.get("data", [])
                for it in items:
                    a = _normalize(it)
                    # Belt-and-braces: keep only rows actually for this issuer
                    if a["company_name"] and (
                        a["company_name"].lower().startswith(variant.lower()[:12])
                        or variant.lower().startswith(a["company_name"].lower()[:12])
                    ):
                        actions.append(a)
            if actions:
                break
    except Exception:
        _record(False)

    if not answered and not actions:
        return [], "blocked"

    # Dedupe identical disclosures (same agency+rating+date) filed on both indices
    seen, unique = set(), []
    for a in actions:
        k = (a["agency"].lower(), a["rating"].lower(), a["date"], a["isin"])
        if k not in seen:
            seen.add(k)
            unique.append(a)

    unique.sort(key=lambda a: _date_key(a["date"]), reverse=True)
    _cache[key] = unique
    return unique, "ok" if unique else "none"
