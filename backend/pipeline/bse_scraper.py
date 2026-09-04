import asyncio
import logging
import re
import time
import httpx

log = logging.getLogger("acer-iq.bse")

# BSE retired its per-company debt *search* endpoints (GetDebtScripsSearchData/w
# and SearchData/w both 302 to error_Bse.html as of Sep 2026). The active-scrip
# master list still works and is strictly better for our use: one fetch gives
# every listed debt instrument with its issuer, so lookups become local dict
# hits instead of N rate-limited round trips.
BSE_SCRIP_MASTER = "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
# Company/equity name search - the live replacement for the retired SearchData/w
BSE_QUOTE_SEARCH = "https://api.bseindia.com/Msource/1D/getQouteSearch.aspx"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer":    "https://www.bseindia.com/",
    "Accept":     "application/json, text/plain, */*",
    "Origin":     "https://www.bseindia.com",
}

# -- Shared client: creating an AsyncClient per call costs ~1s of blocking
# SSL-context setup on Windows and serializes the event loop ------------------
_client: httpx.AsyncClient | None = None


def get_bse_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        # The master list is several MB, so it needs a longer read budget than
        # the old per-company calls did.
        _client = httpx.AsyncClient(timeout=httpx.Timeout(6.0, read=60.0),
                                    headers=_HEADERS)
    return _client


# -- Circuit breaker: when BSE blocks/tarpits us, stop hammering it -----------
_breaker = {"fails": 0, "until": 0.0}


def _bse_tripped() -> bool:
    return time.time() < _breaker["until"]


def _bse_record(ok: bool) -> None:
    if ok:
        _breaker["fails"] = 0
    else:
        _breaker["fails"] += 1
        if _breaker["fails"] >= 3:
            log.warning("BSE breaker open for 10 min after %d failures",
                        _breaker["fails"])
            _breaker["until"] = time.time() + 600
            _breaker["fails"] = 0


# -- Active-scrip master, fetched once and reused -----------------------------
_MASTER_TTL = 12 * 3600
_cache: dict[str, list] = {}
_master: dict[str, list[dict]] = {}
# Every listed name from the same fetch (equity included) - powers company
# autocomplete without a second network call.
_names: list[dict] = []
# Normalized issuer name -> BSE scrip code, for CorpInfo lookups. Equity codes
# are preferred: CorpInfo is an equity-segment endpoint.
_codes: dict[str, str] = {}
_master_at = 0.0
_master_lock: asyncio.Lock | None = None

_LEGAL = re.compile(
    r"\b(LIMITED|LTD|PRIVATE|PVT|PUBLIC|COMPANY|CO|CORPORATION|CORP|INDIA|"
    r"THE|AND)\b", re.I,
)


def _norm(name: str) -> str:
    """Issuer names differ between sources ('Bajaj Finance Ltd.' vs 'Bajaj
    Finance Limited'), so index and look up on a suffix-stripped form."""
    n = re.sub(r"[^A-Za-z0-9 ]", " ", name or "").upper()
    n = _LEGAL.sub(" ", n)
    return " ".join(n.split())


# Bond scrip ids encode coupon and maturity year, with the decimal point
# implied by digit count: '10CAGL27' -> 10.00% due 2027, '805BFL26' -> 8.05%
# due 2026, '1025XYZ30' -> 10.25% due 2030.
_SCRIP_ID_RE = re.compile(r"^(\d{2,4})([A-Z]{2,})(\d{2})$")


def _from_scrip_id(scrip_id: str) -> tuple[str, str]:
    """(coupon_rate, maturity_year) parsed out of a BSE bond scrip id.
    ponytail: naive pattern match on BSE's own id convention - covers plain
    bonds, returns blanks for CPs and odd ids. Swap for the per-scrip debt
    detail endpoint if exact issue/maturity dates ever matter."""
    m = _SCRIP_ID_RE.match((scrip_id or "").upper())
    if not m:
        return "", ""
    coupon, _, yy = m.groups()
    v = int(coupon)
    # BSE leaves the decimal point implied and the digit count is not a reliable
    # guide on its own: '10CAGL27' is 10%, '92LTF29' is 9.2%, '805BFL26' is
    # 8.05%. Scale down until the number lands in a plausible coupon range.
    if len(coupon) <= 2:
        rate = v / 10 if v > 30 else float(v)
    else:
        rate = v / 100
    return (f"{rate:.2f}" if 0 < rate < 30 else ""), f"20{yy}"


def _parse_scrip(row: dict) -> dict:
    coupon, maturity_year = _from_scrip_id(row.get("scrip_id", ""))
    return {
        "isin":            row.get("ISIN_NUMBER", "") or "",
        "security_name":   row.get("scrip_id", "") or row.get("Scrip_Name", "") or "",
        "instrument_type": row.get("Segment", "") or "NCD/Bond",
        "face_value":      str(row.get("FACE_VALUE", "") or ""),
        "issue_date":      "",
        "maturity_date":   maturity_year,
        "coupon_rate":     coupon,
        # BSE's master list carries no rating fields. Leaving these blank is the
        # honest answer - the 7-agency matrix is built from NSE disclosures and
        # CRA press releases in credit_history.py, not from here.
        "credit_rating":   "",
        "rating_agency":   "",
        "status":          row.get("Status", "Active") or "Active",
        "amount_crores":   "",
        "bse_scrip_code":  str(row.get("SCRIP_CD", "") or ""),
        "url":             row.get("NSURL", "") or "",
    }


async def _load_master() -> dict[str, list[dict]]:
    """Fetch and index every active BSE debt scrip by normalized issuer name."""
    global _master, _names, _codes, _master_at, _master_lock
    if _master and time.time() - _master_at < _MASTER_TTL:
        return _master
    if _master_lock is None:
        _master_lock = asyncio.Lock()
    async with _master_lock:
        # Another task may have loaded it while we waited on the lock.
        if _master and time.time() - _master_at < _MASTER_TTL:
            return _master
        if _bse_tripped():
            return _master
        try:
            resp = await get_bse_client().get(
                BSE_SCRIP_MASTER,
                params={"Group": "", "Scripcode": "", "industry": "",
                        "segment": "Debt", "status": "Active"},
            )
            ok = resp.status_code == 200
            _bse_record(ok)
            if not ok:
                log.warning("BSE scrip master returned HTTP %s", resp.status_code)
                return _master
            rows = resp.json()
            if not isinstance(rows, list):
                log.warning("BSE scrip master shape changed: %s", type(rows))
                return _master
        except Exception as e:
            _bse_record(False)
            log.warning("BSE scrip master fetch failed: %s: %s", type(e).__name__, e)
            return _master

        index: dict[str, list[dict]] = {}
        names: dict[str, dict] = {}
        codes: dict[str, str] = {}
        for row in rows:
            issuer = (row.get("Issuer_Name") or row.get("Scrip_Name") or "").strip()
            segment = row.get("Segment") or ""
            code = str(row.get("SCRIP_CD") or "")
            if issuer and issuer.lower() not in names:
                names[issuer.lower()] = {
                    "name": issuer,
                    "bse_code": code,
                    "segment": segment,
                }
            key_n = _norm(issuer)
            if key_n and code and (key_n not in codes or segment == "Equity"):
                codes[key_n] = code
            # Equity and mutual-fund rows are not debt instruments.
            if segment in ("Equity", "MF", "", None):
                continue
            key = _norm(issuer)
            if key:
                index.setdefault(key, []).append(row)

        _master, _names, _codes = index, list(names.values()), codes
        _master_at = time.time()
        _cache.clear()  # per-company results are derived from the master
        log.info("BSE scrip master loaded: %d debt issuers, %d debt scrips, "
                 "%d listed names", len(index),
                 sum(len(v) for v in index.values()), len(_names))
        return _master


async def scrip_code_for(company_name: str) -> str:
    """BSE scrip code for an exact issuer match, or "" if the company is not
    listed. Exact only, on purpose: a loose match would hand CorpInfo the wrong
    company and attach its CIN and board to this lead."""
    await _load_master()
    return _codes.get(_norm(_clean_name_for_bse(company_name)), "")


async def bse_health_check() -> bool:
    """Warm the scrip master once before bulk enrichment. Loading it up front
    means a 60-lead search makes exactly one BSE call, not sixty."""
    return bool(await _load_master())


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
    # RBI registry names carry trailing parentheticals that BSE never has --
    # "L&T Finance Limited (Formerly known as L & T Finance Holdings Limited)",
    # "Auxilo Finserve Private Limited (W.E.F. September 11, 2017)". Stripping
    # them is what makes an exact issuer match possible.
    name = re.sub(r'\s*\(.*$', '', name)
    # Remove trailing punctuation and common legal suffixes that BSE may not have
    name = re.sub(r'\s*\.\s*$', '', name)
    return name.strip()


def _short_name(name: str) -> str:
    """Return a shorter/simpler search term — first 2-3 meaningful words."""
    stop = {"of", "and", "&", "the", "pvt", "ltd", "limited", "private"}
    words = [w for w in name.split() if w.lower() not in stop]
    return " ".join(words[:3])


async def fetch_past_instruments(company_name: str) -> list[dict]:
    """Listed NCD / bond / CP instruments for a company, from the cached BSE
    active-scrip master. Local lookup - no per-company network call."""
    key = company_name.strip().lower()
    if key in _cache:
        return _cache[key]

    index = await _load_master()
    if not index:
        return []  # don't cache a BSE outage as "this company has no debt"

    cleaned = _clean_name_for_bse(company_name)
    # Exact issuer match only. Prefix/containment matching was tried and removed:
    # it silently attributed a parent's debt to a subsidiary ('REC POWER
    # DEVELOPMENT' inheriting 'REC' Ltd's bonds). Showing one company's
    # instruments under another is worse than showing none.
    rows = index.get(_norm(cleaned)) or index.get(_norm(_short_name(cleaned)))

    result = [_parse_scrip(r) for r in (rows or [])[:12]]
    _cache[key] = result
    return result


async def search_bse_companies(query: str, limit: int = 10) -> list[dict]:
    """Company name suggestions for /api/company-suggest.

    BSE's SearchData/w endpoint is retired and getQouteSearch.aspx now answers
    200 with an empty body, so this reads the scrip-master name list that
    _load_master already caches - covering every BSE-listed company and debt
    issuer for zero extra network calls."""
    q = query.strip().lower()
    if len(q) < 2:
        return []
    await _load_master()
    if not _names:
        return []
    starts = [n for n in _names if n["name"].lower().startswith(q)]
    contains = [n for n in _names
                if q in n["name"].lower() and not n["name"].lower().startswith(q)]
    # Prefix matches first, then substring; shortest name wins within each group
    # so "Bajaj Finance Ltd" outranks "Bajaj Finance Securities Ltd".
    ranked = sorted(starts, key=lambda n: len(n["name"])) + \
             sorted(contains, key=lambda n: len(n["name"]))
    return [{"name": n["name"], "bse_code": n["bse_code"],
             "sector": n["segment"]} for n in ranked[:limit]]
