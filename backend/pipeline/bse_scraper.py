import logging
import re

import httpx

from backend.netutil import SourceStatus, cache, limiter

log = logging.getLogger("acer_iq.bse")

CACHE_TTL = 6 * 3600

BSE_DEBT_SEARCH = "https://api.bseindia.com/BseIndiaAPI/api/GetDebtScripsSearchData/w"
BSE_DEBT_ALT    = "https://api.bseindia.com/BseIndiaAPI/api/SearchData/w"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":    "https://www.bseindia.com/",
    "Accept":     "application/json, text/plain, */*",
    "Origin":     "https://www.bseindia.com",
}


def _clean_name_for_bse(name: str) -> str:
    """
    Strip branch / ATM / office suffixes from Overpass names so the BSE
    search finds the parent company.

    Examples:
      "State Bank of India - Kochi Branch"  →  "State Bank of India"
      "HDFC Bank ATM"                        →  "HDFC Bank"
      "Bajaj Finance Ltd. – Regional Office" →  "Bajaj Finance"
    """
    # Remove a trailing dash/em-dash segment that ends in Branch/ATM/Office etc.
    # ("- Kochi Branch", "– Regional Office", ...)
    name = re.sub(
        r'\s*[-–—][^-–—]*\b(Branch|ATM|Office|HO|Head Office|Regional Office|Zonal Office'
        r'|Corporate Office|Registered Office|Extension Counter|Service Centre|Unit)\b.*$',
        '', name, flags=re.IGNORECASE,
    )
    # Remove trailing standalone branch/ATM indicators
    name = re.sub(
        r'\s+(Branch|ATM|Extension Counter|Kiosk)$',
        '', name, flags=re.IGNORECASE,
    )
    # Remove trailing punctuation and common legal suffixes that BSE may not have
    name = re.sub(r'\s*\.\s*$', '', name)
    return name.strip()


def _short_name(name: str) -> str:
    """Return a shorter/simpler search term — first 2-3 meaningful words."""
    stop = {"of", "and", "&", "the", "pvt", "ltd", "limited", "private"}
    words = [w for w in name.split() if w.lower() not in stop]
    return " ".join(words[:3])


def _parse_bse_item(item: dict) -> dict:
    return {
        "isin":            item.get("ISIN_NO",       item.get("ISIN", "")),
        "security_name":   item.get("SCRIP_NAME",    item.get("SECURITY_NAME", item.get("SecurityName", ""))),
        "instrument_type": item.get("CATEGORY",      item.get("Category", item.get("INSTRUMENT", "NCD/Bond"))),
        "face_value":      str(item.get("FACE_VALUE",  item.get("FaceValue", ""))),
        "issue_date":      item.get("ISSUE_DATE",     item.get("IssueDate", "")),
        "maturity_date":   item.get("MATURITY_DATE",  item.get("REDEMPTION_DATE", item.get("MaturityDate", ""))),
        "coupon_rate":     str(item.get("COUPON_RATE", item.get("CouponRate", ""))),
        "credit_rating":   item.get("CREDIT_RATING",  item.get("CreditRating", "")),
        "rating_agency":   item.get("RATING_AGENCY",  item.get("RatingAgency", "")),
        "status":          item.get("STATUS",          item.get("Status", "Listed")),
        "amount_crores":   str(item.get("ISSUE_SIZE",  item.get("IssueSize", item.get("Amount", "")))),
    }


def parse_debt_response(data: dict) -> list[dict]:
    """Pure parser for a BSE debt-search JSON payload (tested with fixtures)."""
    rows = data.get("Table", data.get("data", []))
    if isinstance(rows, list) and rows:
        return [_parse_bse_item(i) for i in rows[:12]]
    return []


async def _search_bse_debt(search_name: str, client: httpx.AsyncClient,
                           status: SourceStatus | None) -> list[dict] | None:
    """Primary BSE debt endpoint. None = request failed (vs [] = no match)."""
    try:
        resp = await client.get(
            BSE_DEBT_SEARCH,
            params={"scripname": search_name, "category": "", "subcateg": "", "status": "", "exDate": ""},
        )
        if resp.status_code == 200:
            if status:
                status.ok("bse")
            return parse_debt_response(resp.json())
        log.warning("BSE debt search HTTP %s for %r", resp.status_code, search_name)
        if status:
            status.fail("bse", f"HTTP {resp.status_code}")
    except Exception as exc:
        log.warning("BSE debt search failed for %r: %r", search_name, exc)
        if status:
            status.fail("bse", repr(exc))
    return None


async def _search_bse_alt(search_name: str, client: httpx.AsyncClient,
                          status: SourceStatus | None) -> list[dict] | None:
    """Alternate BSE search endpoint filtered to DEBT tab."""
    try:
        resp = await client.get(
            BSE_DEBT_ALT,
            params={"strText": search_name, "flag": "0", "Membertype": "S",
                    "pageno": "1", "tab": "DEBT"},
        )
        if resp.status_code == 200:
            if status:
                status.ok("bse")
            return parse_debt_response(resp.json())
        log.warning("BSE alt search HTTP %s for %r", resp.status_code, search_name)
        if status:
            status.fail("bse", f"HTTP {resp.status_code}")
    except Exception as exc:
        log.warning("BSE alt search failed for %r: %r", search_name, exc)
        if status:
            status.fail("bse", repr(exc))
    return None


async def fetch_past_instruments(company_name: str,
                                 status: SourceStatus | None = None) -> list[dict]:
    """
    Fetch past NCD/Bond/Debt issuances for a company from BSE.

    Cached 6h per company; concurrency capped via the 'bse' limiter.

    Strategy:
    1. Clean name (remove branch/ATM suffixes from Overpass results)
    2. Try full cleaned name on primary endpoint
    3. Try full cleaned name on alternate endpoint
    4. Try short name (first 2-3 words) on both endpoints
    """
    cleaned = _clean_name_for_bse(company_name)
    short   = _short_name(cleaned)

    key = "bse_instruments:" + cleaned.lower()
    hit = cache.get(key)
    if hit is not None:
        if status:
            status.ok("bse")
        return hit

    async with limiter("bse"):
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
            attempts = [(_search_bse_debt, cleaned), (_search_bse_alt, cleaned)]
            if short and short.lower() != cleaned.lower():
                attempts += [(_search_bse_debt, short), (_search_bse_alt, short)]

            any_success = False
            for fn, name in attempts:
                instruments = await fn(name, client, status)
                if instruments is None:  # request failed — try next strategy
                    continue
                any_success = True
                if instruments:
                    cache.set_result(key, instruments, CACHE_TTL)
                    return instruments

    # Only cache "no instruments" when BSE actually answered; if every
    # attempt errored, leave it uncached so the next request retries.
    if any_success:
        cache.set_result(key, [], CACHE_TTL)
    return []
