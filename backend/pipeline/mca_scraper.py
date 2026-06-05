import httpx
from bs4 import BeautifulSoup

ZAUBA_SEARCH = "https://www.zaubacorp.com/company-search"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


async def fetch_mca_data(company_name: str) -> dict:
    """
    Scrape Zauba Corp for CIN, directors, incorporation date.
    Returns empty dict on any failure — caller must handle gracefully.
    """
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS, follow_redirects=True) as client:
            search_resp = await client.get(
                ZAUBA_SEARCH, params={"search": company_name}
            )
            if search_resp.status_code != 200:
                return _empty()

            soup = BeautifulSoup(search_resp.text, "lxml")

            # Find first company row in search results table
            rows = soup.select("table tbody tr")
            if not rows:
                return _empty()

            first_row = rows[0]
            cells = first_row.find_all("td")
            if len(cells) < 3:
                return _empty()

            cin = cells[0].get_text(strip=True)
            company_link_tag = cells[1].find("a")
            if not company_link_tag:
                return _empty()

            company_url = company_link_tag.get("href", "")
            if not company_url.startswith("http"):
                company_url = f"https://www.zaubacorp.com{company_url}"

            # Fetch company detail page
            detail_resp = await client.get(company_url)
            if detail_resp.status_code != 200:
                return {"cin": cin, "incorporation_date": "", "directors": [], "registered_address": ""}

            detail_soup = BeautifulSoup(detail_resp.text, "lxml")

            inc_date = _extract_label(detail_soup, "Date of Incorporation")
            reg_address = _extract_label(detail_soup, "Registered Address")

            directors = _extract_directors(detail_soup)

            return {
                "cin": cin,
                "incorporation_date": inc_date,
                "registered_address": reg_address,
                "directors": directors,
            }

    except Exception:
        return _empty()


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
        # Zauba shows directors in a table with DIN, name, designation
        tables = soup.find_all("table")
        for table in tables:
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if "din" in headers or "director" in " ".join(headers):
                for row in table.find_all("tr")[1:]:
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        name = cells[0].get_text(strip=True) if len(cells) > 0 else ""
                        din = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                        designation = cells[2].get_text(strip=True) if len(cells) > 2 else "Director"
                        if name:
                            directors.append({
                                "name": name,
                                "din": din,
                                "designation": designation,
                                "linkedin_url": (
                                    f"https://www.linkedin.com/search/results/people/"
                                    f"?keywords={name.replace(' ', '+')}"
                                ),
                            })
    except Exception:
        pass
    return directors[:10]


def _empty() -> dict:
    return {"cin": "", "incorporation_date": "", "directors": [], "registered_address": ""}
