"""
Company master data — CIN, directors, incorporation date, registered address.

Source chain:
1. Cached BSE scrip master → scrip code → CorpInfo  (CIN in Table3.fld_cin,
   directors in Table, address in Table1 — covers listed + debt issuers)
2. Zauba Corp direct scrape                         (unlisted / private companies)
"""

import logging
import re

import httpx

from backend.pipeline.bse_scraper import (
    _bse_record, _bse_tripped, get_bse_client, scrip_code_for,
)

log = logging.getLogger("acer-iq.mca")

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

_web_client = None


def _get_web_client():
    global _web_client
    if _web_client is None or _web_client.is_closed:
        _web_client = httpx.AsyncClient(timeout=8, headers=_WEB_HEADERS,
                                        follow_redirects=True)
    return _web_client


_CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")


# ── BSE CorpInfo — returns CIN, directors, address ────────────────────────────

async def _bse_corp_info(scrip_code: str) -> dict:
    """
    Call BSE CorpInfo endpoint for a known scrip code.
    Returns dict with cin, directors, registered_address, incorporation_date.
    """
    if _bse_tripped():
        return {}
    try:
        client = get_bse_client()
        if True:
            r = await client.get(
                "https://api.bseindia.com/BseIndiaAPI/api/CorpInfo/w",
                params={"scripcode": scrip_code},
            )
            _bse_record(r.status_code == 200)
            if r.status_code != 200:
                return {}

            d = r.json()

            # ── Table3 — CIN, Industry, Listing date ─────────────────────────
            t3 = d.get("Table3", [{}])
            row3 = t3[0] if isinstance(t3, list) and t3 else {}
            cin  = row3.get("fld_cin", "").strip()

            # ── Table1 — Registered address ──────────────────────────────────
            t1   = d.get("Table1", [{}])
            row1 = t1[0] if isinstance(t1, list) and t1 else {}
            addr_parts = [
                row1.get("Address", ""),
                row1.get("City", ""),
                row1.get("State", ""),
            ]
            address = ", ".join(p for p in addr_parts if p).strip()

            # ── Table — Directors ─────────────────────────────────────────────
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

            # ── Dates ────────────────────────────────────────────────────────
            # BSE's listing date is NOT the incorporation date — Tata Capital
            # incorporated in 1991 but listed in 2025. The CIN encodes the real
            # year of incorporation at chars 8:12, so use that and report the
            # listing date separately instead of passing it off as incorporation.
            listing_raw = row3.get("lISTING_DATE", "")
            listing_date = listing_raw[:10] if listing_raw else ""
            inc_year = cin[8:12] if _CIN_RE.match(cin) else ""
            inc_date = inc_year if inc_year.isdigit() else ""

            if cin:
                return {
                    "cin":                cin,
                    "incorporation_date": inc_date,
                    "listing_date":       listing_date,
                    "registered_address": address,
                    "directors":          directors,
                }
    except Exception:
        pass
    return {}


# ── BSE equity search → scrip code → CorpInfo ────────────────────────────────

async def _bse_scrip_cin(company_name: str) -> dict:
    """Name -> BSE scrip code -> CorpInfo (CIN, directors, registered address).

    BSE's SearchData/w name-search endpoint is retired (302 to error_Bse.html),
    so the scrip code now comes from the cached active-scrip master in
    bse_scraper. CorpInfo itself still works, so this restores CIN and board
    data for every BSE-listed company and debt issuer.
    """
    if _bse_tripped():
        return {}
    try:
        scrip_code = await scrip_code_for(company_name)
    except Exception as e:
        log.warning("BSE scrip lookup failed for %r: %s: %s",
                    company_name, type(e).__name__, e)
        return {}
    if not scrip_code:
        return {}
    return await _bse_corp_info(scrip_code)


async def _zauba_cin(company_name: str) -> dict:
    """Scrape Zauba Corp search page — works for unlisted/private companies."""
    try:
        from bs4 import BeautifulSoup
        client = _get_web_client()
        if True:
            r = await client.get(
                "https://www.zaubacorp.com/company-search",
                params={"search": company_name},
            )
            if r.status_code != 200:
                return {}

            soup = BeautifulSoup(r.text, "lxml")
            rows = soup.select("table tbody tr")
            if not rows:
                return {}

            cells = rows[0].find_all("td")
            if len(cells) < 2:
                return {}

            cin = cells[0].get_text(strip=True)
            if not _CIN_RE.match(cin):
                return {}

            link = cells[1].find("a")
            if not link:
                return {"cin": cin, "incorporation_date": "",
                        "registered_address": "", "directors": []}

            url = link.get("href", "")
            if not url.startswith("http"):
                url = f"https://www.zaubacorp.com{url}"

            detail = await client.get(url)
            if detail.status_code != 200:
                return {"cin": cin, "incorporation_date": "",
                        "registered_address": "", "directors": []}

            dsoup    = BeautifulSoup(detail.text, "lxml")
            inc_date = _extract_label(dsoup, "Date of Incorporation")
            address  = _extract_label(dsoup, "Registered Address")
            directors = _extract_directors(dsoup)

            return {
                "cin":                cin,
                "incorporation_date": inc_date,
                "registered_address": address,
                "directors":          directors,
            }
    except Exception:
        pass
    return {}


# ── Public entry point ────────────────────────────────────────────────────────

_cache: dict[str, dict] = {}


async def fetch_mca_data(company_name: str, skip_zauba: bool = False) -> dict:
    """
    BSE equity → BSE debt → Zauba Corp fallback.
    Returns empty dict on total failure. Results are cached per process.

    skip_zauba: pass True for companies unlikely to be on Zauba or when speed
    matters more than director coverage (e.g. bulk search enrichment).
    """
    key = company_name.strip().lower()
    if key in _cache:
        return _cache[key]

    result = await _bse_scrip_cin(company_name)
    if not result.get("cin") and not skip_zauba:
        result = await _zauba_cin(company_name)

    result = result if result.get("cin") else _empty()
    _cache[key] = result
    return result


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
        from bs4 import BeautifulSoup
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
