import re
import httpx

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
    # Remove everything after a dash/em-dash followed by Branch/ATM/Office etc.
    name = re.sub(
        r'\s*[-–—]\s*(Branch|ATM|Office|HO|Head Office|Regional Office|Zonal Office'
        r'|Corporate Office|Registered Office|Extension Counter|Service Centre|Unit).*$',
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


async def _search_bse_debt(search_name: str, client: httpx.AsyncClient) -> list[dict]:
    """Try primary BSE debt endpoint."""
    try:
        resp = await client.get(
            BSE_DEBT_SEARCH,
            params={"scripname": search_name, "category": "", "subcateg": "", "status": "", "exDate": ""},
        )
        if resp.status_code == 200:
            data = resp.json()
            rows = data.get("Table", data.get("data", []))
            if isinstance(rows, list) and rows:
                return [_parse_bse_item(i) for i in rows[:12]]
    except Exception:
        pass
    return []


async def _search_bse_alt(search_name: str, client: httpx.AsyncClient) -> list[dict]:
    """Try alternate BSE search endpoint filtered to DEBT tab."""
    try:
        resp = await client.get(
            BSE_DEBT_ALT,
            params={"strText": search_name, "flag": "0", "Membertype": "S",
                    "pageno": "1", "tab": "DEBT"},
        )
        if resp.status_code == 200:
            data = resp.json()
            rows = data.get("Table", data.get("data", []))
            if isinstance(rows, list) and rows:
                return [_parse_bse_item(i) for i in rows[:12]]
    except Exception:
        pass
    return []


async def fetch_past_instruments(company_name: str) -> list[dict]:
    """
    Fetch past NCD/Bond/Debt issuances for a company from BSE.

    Strategy:
    1. Clean name (remove branch/ATM suffixes from Overpass results)
    2. Try full cleaned name on primary endpoint
    3. Try full cleaned name on alternate endpoint
    4. Try short name (first 2-3 words) on both endpoints
    """
    cleaned = _clean_name_for_bse(company_name)
    short   = _short_name(cleaned)

    async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
        # Attempt 1: full cleaned name, primary endpoint
        instruments = await _search_bse_debt(cleaned, client)
        if instruments:
            return instruments

        # Attempt 2: full cleaned name, alternate endpoint
        instruments = await _search_bse_alt(cleaned, client)
        if instruments:
            return instruments

        # Attempt 3: short name on primary (handles abbreviations)
        if short and short.lower() != cleaned.lower():
            instruments = await _search_bse_debt(short, client)
            if instruments:
                return instruments

            # Attempt 4: short name, alternate endpoint
            instruments = await _search_bse_alt(short, client)
            if instruments:
                return instruments

    return []
