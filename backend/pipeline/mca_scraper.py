"""
Company master data — CIN, directors, dates, registered address.

Source chain:
1. BSE equity search → CorpInfo endpoint  (CIN in Table3.fld_cin, directors in Table,
                                            address in Table1 — fast, reliable, no auth)
2. BSE debt search → CorpInfo             (same flow for debt-only NBFC issuers)
3. Zauba Corp direct scrape               (unlisted / private companies; also the
                                            only source here that accepts a CIN query)

Date semantics (P6 fix): BSE only knows the LISTING date — that is returned as
`listing_date`, never as `incorporation_date`. A real incorporation date only
comes from Zauba/MCA.
"""

import logging
import re

import httpx

from backend.netutil import SourceStatus, cache, limiter

log = logging.getLogger("acer_iq.mca")

CACHE_TTL = 24 * 3600

_BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":    "https://www.bseindia.com/",
    "Accept":     "application/json, text/plain, */*",
    "Origin":     "https://www.bseindia.com",
}

_WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")


def is_cin(query: str) -> bool:
    return bool(CIN_RE.match(query.strip().upper()))


# ── BSE CorpInfo — returns CIN, directors, address, listing date ─────────────

def parse_corp_info(d: dict) -> dict:
    """Pure parser for the BSE CorpInfo JSON payload (tested with fixtures)."""
    # Table3 — CIN, industry, listing date
    t3 = d.get("Table3", [{}])
    row3 = t3[0] if isinstance(t3, list) and t3 else {}
    cin  = (row3.get("fld_cin") or "").strip()

    # Table1 — registered address
    t1   = d.get("Table1", [{}])
    row1 = t1[0] if isinstance(t1, list) and t1 else {}
    addr_parts = [row1.get("Address", ""), row1.get("City", ""), row1.get("State", "")]
    address = ", ".join(p for p in addr_parts if p).strip()

    # Table — directors
    directors = []
    for dr in (d.get("Table") or [])[:10]:
        first = (dr.get("sFirstname") or "").strip()
        last  = (dr.get("sLastname")  or "").strip()
        name  = f"{first} {last}".strip()
        if not name:
            continue
        directors.append({
            "name":        name,
            "din":         dr.get("sDIN", ""),
            "designation": (dr.get("sDesignation") or "Director").strip(),
            "linkedin_url": (
                "https://www.linkedin.com/search/results/people/"
                f"?keywords={name.replace(' ', '+')}"
            ),
        })

    # BSE listing date is NOT the incorporation date — keep them separate
    listing_raw  = row3.get("lISTING_DATE", "")
    listing_date = listing_raw[:10] if listing_raw else ""

    if not cin:
        return {}
    return {
        "cin":                cin,
        "incorporation_date": "",            # BSE does not know this
        "listing_date":       listing_date,
        "registered_address": address,
        "directors":          directors,
    }


async def _bse_corp_info(scrip_code: str, status: SourceStatus | None) -> dict:
    """Call BSE CorpInfo endpoint for a known scrip code."""
    try:
        async with httpx.AsyncClient(timeout=10, headers=_BSE_HEADERS) as client:
            r = await client.get(
                "https://api.bseindia.com/BseIndiaAPI/api/CorpInfo/w",
                params={"scripcode": scrip_code},
            )
            if r.status_code != 200:
                log.warning("BSE CorpInfo HTTP %s for scrip %s", r.status_code, scrip_code)
                if status:
                    status.fail("bse", f"CorpInfo HTTP {r.status_code}")
                return {}
            if status:
                status.ok("bse")
            return parse_corp_info(r.json())
    except Exception as exc:
        log.warning("BSE CorpInfo failed for scrip %s: %r", scrip_code, exc)
        if status:
            status.fail("bse", repr(exc))
    return {}


async def _bse_search_scrip(company_name: str, tab: str,
                            status: SourceStatus | None) -> dict:
    """Search a BSE segment ('EQ' or 'DEBT') by name, then fetch CorpInfo."""
    try:
        async with httpx.AsyncClient(timeout=10, headers=_BSE_HEADERS) as client:
            r = await client.get(
                "https://api.bseindia.com/BseIndiaAPI/api/SearchData/w",
                params={"strText": company_name, "flag": "0",
                        "Membertype": "S", "pageno": "1", "tab": tab},
            )
            if r.status_code != 200:
                if status:
                    status.fail("bse", f"SearchData HTTP {r.status_code}")
                return {}
            if status:
                status.ok("bse")
            rows = r.json().get("Table") or []
            if not rows:
                return {}

            # Debt rows sometimes carry CIN directly
            direct_cin = (rows[0].get("CIN") or rows[0].get("cin") or "").strip()
            if direct_cin and CIN_RE.match(direct_cin):
                return {"cin": direct_cin, "incorporation_date": "",
                        "listing_date": "", "registered_address": "", "directors": []}

            scrip_code = str(rows[0].get("SCRIP_CD") or rows[0].get("scripCd") or "").strip()
            if not scrip_code:
                return {}
    except Exception as exc:
        log.warning("BSE %s search failed for %r: %r", tab, company_name, exc)
        if status:
            status.fail("bse", repr(exc))
        return {}

    return await _bse_corp_info(scrip_code, status)


# ── Zauba Corp scrape — fallback for unlisted/private companies & CIN query ──

async def _zauba_lookup(query: str, status: SourceStatus | None) -> dict:
    """Scrape Zauba Corp search page — accepts a company name OR a CIN."""
    try:
        from bs4 import BeautifulSoup
        async with httpx.AsyncClient(
            timeout=15, headers=_WEB_HEADERS, follow_redirects=True
        ) as client:
            r = await client.get(
                "https://www.zaubacorp.com/company-search",
                params={"search": query},
            )
            if r.status_code != 200:
                log.warning("Zauba search HTTP %s for %r", r.status_code, query)
                if status:
                    status.fail("zauba", f"HTTP {r.status_code}")
                return {}
            if status:
                status.ok("zauba")

            soup = BeautifulSoup(r.text, "lxml")
            rows = soup.select("table tbody tr")
            if not rows:
                return {}

            cells = rows[0].find_all("td")
            if len(cells) < 2:
                return {}

            cin = cells[0].get_text(strip=True)
            if not CIN_RE.match(cin):
                return {}

            base = {"cin": cin, "incorporation_date": "", "listing_date": "",
                    "registered_address": "", "directors": [],
                    "name": cells[1].get_text(strip=True)}

            link = cells[1].find("a")
            if not link:
                return base

            url = link.get("href", "")
            if not url.startswith("http"):
                url = f"https://www.zaubacorp.com{url}"

            detail = await client.get(url)
            if detail.status_code != 200:
                return base

            dsoup = BeautifulSoup(detail.text, "lxml")
            base["incorporation_date"] = _extract_label(dsoup, "Date of Incorporation")
            base["registered_address"] = _extract_label(dsoup, "Registered Address")
            base["directors"]          = _extract_directors(dsoup)
            return base
    except Exception as exc:
        log.warning("Zauba lookup failed for %r: %r", query, exc)
        if status:
            status.fail("zauba", repr(exc))
    return {}


# ── Public entry point ────────────────────────────────────────────────────────

async def fetch_mca_data(query: str, status: SourceStatus | None = None) -> dict:
    """
    Company master data for a name or a CIN.

    Name → BSE equity → BSE debt → Zauba Corp fallback.
    CIN  → straight to Zauba (the BSE name-search endpoints cannot resolve a
           CIN — that was the P6 /api/company-credit bug).
    Cached 24h. Returns empty-shape dict on total failure.
    """
    query = query.strip()
    key = "mca:" + query.lower()
    hit = cache.get(key)
    if hit is not None:
        return hit

    if is_cin(query):
        async with limiter("zauba"):
            result = await _zauba_lookup(query.upper(), status)
        if result.get("cin"):
            cache.set_result(key, result, CACHE_TTL)
            return result
        cache.set_result(key, _empty(), CACHE_TTL)
        return _empty()

    async with limiter("bse"):
        result = await _bse_search_scrip(query, "EQ", status)
        if not result.get("cin"):
            result = await _bse_search_scrip(query, "DEBT", status)
    if result.get("cin"):
        cache.set_result(key, result, CACHE_TTL)
        return result

    async with limiter("zauba"):
        result = await _zauba_lookup(query, status)
    if result.get("cin"):
        cache.set_result(key, result, CACHE_TTL)
        return result

    cache.set_result(key, _empty(), CACHE_TTL)
    return _empty()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_label(soup, label: str) -> str:
    try:
        tag = soup.find(string=lambda t: t and label.lower() in t.lower())
        if tag and tag.parent:
            sib = tag.parent.find_next_sibling()
            if sib:
                return sib.get_text(strip=True)
    except Exception:
        pass
    return ""


def _extract_directors(soup) -> list[dict]:
    directors = []
    try:
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if "din" in headers or "director" in " ".join(headers):
                for row in table.find_all("tr")[1:]:
                    cells = row.find_all("td")
                    name = cells[0].get_text(strip=True) if cells else ""
                    din  = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                    desig = cells[2].get_text(strip=True) if len(cells) > 2 else "Director"
                    if name:
                        directors.append({
                            "name": name, "din": din, "designation": desig,
                            "linkedin_url": f"https://www.linkedin.com/search/results/people/?keywords={name.replace(' ', '+')}",
                        })
    except Exception:
        pass
    return directors[:10]


def _empty() -> dict:
    return {"cin": "", "incorporation_date": "", "listing_date": "",
            "directors": [], "registered_address": ""}
