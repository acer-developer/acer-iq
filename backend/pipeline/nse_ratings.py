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


# ── Rating actions mined from corporate announcements (listed companies) ──────
# Equity-listed companies file rating actions as 'Credit Rating' announcements;
# the structured credit-rating feed often misses them entirely (e.g. Suzlon).

NSE_ANNOUNCEMENTS = "https://www.nseindia.com/api/corporate-announcements"

_MONTHS = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05",
           "Jun": "06", "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10",
           "Nov": "11", "Dec": "12"}

_AGENCY_RE = re.compile(
    r"\b(CRISIL|ICRA|CARE(?:\s+Ratings|EDGE)?|India\s+Ratings|IND[- ]RA|Acuit[eé]|"
    r"Brickwork|BWR|Infomerics|IVR|SMERA|Fitch)\b", re.I)

_RATING_RE = re.compile(
    r"\b((?:CRISIL|ICRA|CARE|IND|BWR|IVR|Acuite|Crisil|Ind)?\s?"
    r"(?:AAA|AA\+|AA-|AA|A1\+|A1|A2\+|A2|A3\+|A3|A4\+|A4|A\+|A-|"
    r"BBB\+|BBB-|BBB|BB\+|BB-|BB|B\+|B-|D)"
    r"(?:\s*\(?(?:Stable|Positive|Negative|CE|SO)\)?)?)", re.I)

_ACTION_RE = re.compile(
    r"\b(upgrad\w*|downgrad\w*|reaffirm\w*|re-affirm\w*|withdraw\w*|assign\w*|"
    r"revis\w*|placed on watch|rating watch)\b", re.I)


def _ann_date(an_dt: str) -> str:
    """'30-Jul-2025 17:31:05' → '30-07-2025'."""
    m = re.match(r"(\d{2})-([A-Za-z]{3})-(\d{4})", an_dt or "")
    if not m:
        return ""
    return f"{m.group(1)}-{_MONTHS.get(m.group(2).title(), '01')}-{m.group(3)}"


async def fetch_announcement_ratings(symbol: str, company_name: str) -> list[dict]:
    """Mine 'Credit Rating' announcements for an NSE symbol into rating actions.
    Less structured than the credit-rating feed, but actually complete."""
    if not symbol or _tripped():
        return []
    client = _get_client()
    try:
        await _warmup(client)
        r = await client.get(NSE_ANNOUNCEMENTS,
                             params={"index": "equities", "symbol": symbol})
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            _record(False)
            return []
        _record(True)
        data = r.json()
        items = data if isinstance(data, list) else data.get("data", [])
    except Exception:
        _record(False)
        return []

    actions: list[dict] = []
    for it in items:
        desc = str(it.get("desc") or "")
        text = str(it.get("attchmntText") or "")
        blob = f"{desc} {text}"
        if "credit rating" not in blob.lower() and not (
            "rating" in blob.lower() and _AGENCY_RE.search(blob)
        ):
            continue
        agency_m = _AGENCY_RE.search(blob)
        rating_m = _RATING_RE.search(text)
        action_m = _ACTION_RE.search(blob)
        actions.append({
            "agency": agency_m.group(1) if agency_m else "Disclosed (see filing)",
            "rating": (rating_m.group(1).strip() if rating_m else "") or "See filing",
            "action": (action_m.group(1).title() if action_m else "Credit Rating disclosure"),
            "date": _ann_date(str(it.get("an_dt") or "")),
            "isin": "",
            "company_name": company_name,
            "detail": text[:220],
            "attachment": str(it.get("attchmntFile") or ""),
        })
    actions.sort(key=lambda a: _date_key(a["date"]), reverse=True)
    return actions[:40]


async def fetch_rating_actions(company_name: str, symbol: str = "") -> tuple[list[dict], str]:
    """
    Returns (actions, status).
    status: "ok"        — NSE answered, actions found
            "none"      — NSE answered, no disclosures matched this name
            "blocked"   — NSE unreachable / blocking us (data NOT verified)
    Actions sorted newest first. Combines the structured credit-rating feed
    (issuer exact match) with rating actions mined from the company's
    'Credit Rating' announcements (by NSE symbol, for listed companies).
    """
    key = f"{company_name.strip().lower()}|{symbol.strip().upper()}"
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

    # Announcements-mined actions for listed companies (covers the many
    # issuers the structured feed misses, e.g. Suzlon)
    try:
        ann = await fetch_announcement_ratings(symbol, company_name)
        if ann:
            answered = True
            actions.extend(ann)
    except Exception:
        pass

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
