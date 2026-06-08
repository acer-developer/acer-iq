"""
Company master data — CIN, directors, incorporation date, registered address.

Source priority:
1. BSE Equity company master  — instant, free, no auth, works for listed companies
2. BSE Debt company search    — for debt-only issuers not in equity segment
3. Zauba Corp scrape          — fallback for unlisted / private companies
"""

import re
import httpx
from bs4 import BeautifulSoup

_BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":    "https://www.bseindia.com/",
    "Accept":     "application/json, text/plain, */*",
    "Origin":     "https://www.bseindia.com",
}

_WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Source 1: BSE Equity company master ───────────────────────────────────────

async def _bse_equity_cin(company_name: str) -> dict:
    """
    Search BSE equity segment by name → get scrip code → fetch company master
    which includes CIN, registered address, incorporation date.
    Works for all BSE equity-listed companies (most large banks, NBFCs, corporates).
    """
    try:
        async with httpx.AsyncClient(timeout=10, headers=_BSE_HEADERS) as client:
            # Step 1: search equity by name
            search = await client.get(
                "https://api.bseindia.com/BseIndiaAPI/api/SearchData/w",
                params={"strText": company_name, "flag": "0",
                        "Membertype": "S", "pageno": "1", "tab": "EQ"},
            )
            if search.status_code != 200:
                return {}

            rows = (search.json().get("Table") or [])
            if not rows:
                return {}

            scrip_code = str(
                rows[0].get("SCRIP_CD") or rows[0].get("scripCd") or ""
            ).strip()
            if not scrip_code:
                return {}

            # Step 2: fetch company master data
            detail = await client.get(
                "https://api.bseindia.com/BseIndiaAPI/api/CompanyInfoFulData/w",
                params={"scripcd": scrip_code},
            )
            if detail.status_code != 200:
                return {}

            d = detail.json()
            # BSE returns a flat object with varying key names
            cin  = (d.get("CIN") or d.get("cin") or "").strip()
            addr = (d.get("RegisteredOffice") or d.get("Registered_Office")
                    or d.get("Address") or "").strip()
            inc  = (d.get("DateOfIncorporation") or d.get("Incorporation_Date")
                    or d.get("IncorpDate") or "").strip()
            name_bse = (d.get("CompanyName") or d.get("COMPANYNAME") or "").strip()

            if cin:
                return {
                    "cin":                  cin,
                    "incorporation_date":   _fmt_date(inc),
                    "registered_address":   addr,
                    "directors":            [],   # BSE master doesn't include directors
                    "name":                 name_bse,
                }
    except Exception:
        pass
    return {}


# ── Source 2: BSE Debt company info ───────────────────────────────────────────

async def _bse_debt_cin(company_name: str) -> dict:
    """
    Try BSE debt segment company info — covers NBFC/debt-only issuers
    that may not have an equity listing.
    """
    try:
        async with httpx.AsyncClient(timeout=10, headers=_BSE_HEADERS) as client:
            search = await client.get(
                "https://api.bseindia.com/BseIndiaAPI/api/SearchData/w",
                params={"strText": company_name, "flag": "0",
                        "Membertype": "S", "pageno": "1", "tab": "DEBT"},
            )
            if search.status_code != 200:
                return {}

            rows = (search.json().get("Table") or [])
            if not rows:
                return {}

            # Some debt rows carry CIN directly
            cin = (rows[0].get("CIN") or rows[0].get("cin") or "").strip()
            if cin:
                return {
                    "cin":                cin,
                    "incorporation_date": "",
                    "registered_address": "",
                    "directors":          [],
                    "name":               (rows[0].get("SCRIP_NAME") or
                                           rows[0].get("CompanyName") or "").strip(),
                }
    except Exception:
        pass
    return {}


# ── Source 3: Zauba Corp scrape (fallback) ────────────────────────────────────

async def _zauba_cin(company_name: str) -> dict:
    """Scrape Zauba Corp — covers unlisted / private companies."""
    try:
        async with httpx.AsyncClient(
            timeout=15, headers=_WEB_HEADERS, follow_redirects=True
        ) as client:
            search_resp = await client.get(
                "https://www.zaubacorp.com/company-search",
                params={"search": company_name},
            )
            if search_resp.status_code != 200:
                return {}

            soup  = BeautifulSoup(search_resp.text, "lxml")
            rows  = soup.select("table tbody tr")
            if not rows:
                return {}

            cells = rows[0].find_all("td")
            if len(cells) < 2:
                return {}

            cin = cells[0].get_text(strip=True)
            if not re.match(r'^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$', cin):
                return {}   # Not a valid CIN pattern — skip

            link_tag = cells[1].find("a")
            if not link_tag:
                return {"cin": cin, "incorporation_date": "",
                        "registered_address": "", "directors": []}

            url = link_tag.get("href", "")
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

async def fetch_mca_data(company_name: str) -> dict:
    """
    Try BSE equity → BSE debt → Zauba Corp in order.
    Returns empty dict on total failure — caller handles gracefully.
    """
    # 1. BSE Equity (fastest, most reliable for listed companies)
    result = await _bse_equity_cin(company_name)
    if result.get("cin"):
        return result

    # 2. BSE Debt (for debt-only issuers)
    result = await _bse_debt_cin(company_name)
    if result.get("cin"):
        return result

    # 3. Zauba Corp scrape (unlisted / private)
    result = await _zauba_cin(company_name)
    if result.get("cin"):
        return result

    return _empty()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_date(raw: str) -> str:
    """Normalise BSE date formats like '19770101' or '1977-01-01' to '01 Jan 1977'."""
    if not raw:
        return ""
    raw = raw.strip()
    # Already readable
    if len(raw) > 8 and not raw.isdigit():
        return raw
    # YYYYMMDD
    if len(raw) == 8 and raw.isdigit():
        try:
            from datetime import datetime
            return datetime.strptime(raw, "%Y%m%d").strftime("%d %b %Y")
        except Exception:
            return raw
    return raw


def _extract_label(soup: BeautifulSoup, label: str) -> str:
    try:
        tag = soup.find(string=lambda t: t and label.lower() in t.lower())
        if tag and tag.parent:
            sibling = tag.parent.find_next_sibling()
            if sibling:
                return sibling.get_text(strip=True)
    except Exception:
        pass
    return ""


def _extract_directors(soup: BeautifulSoup) -> list[dict]:
    directors = []
    try:
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if "din" in headers or "director" in " ".join(headers):
                for row in table.find_all("tr")[1:]:
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        name        = cells[0].get_text(strip=True)
                        din         = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                        designation = cells[2].get_text(strip=True) if len(cells) > 2 else "Director"
                        if name:
                            directors.append({
                                "name":        name,
                                "din":         din,
                                "designation": designation,
                                "linkedin_url": (
                                    "https://www.linkedin.com/search/results/people/"
                                    f"?keywords={name.replace(' ', '+')}"
                                ),
                            })
    except Exception:
        pass
    return directors[:10]


def _empty() -> dict:
    return {"cin": "", "incorporation_date": "", "directors": [], "registered_address": ""}
