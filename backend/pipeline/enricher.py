import re
import httpx
from backend.config import settings

HUNTER_BASE = "https://api.hunter.io/v2"

EXEC_KEYWORDS = {
    "cfo", "chief financial", "finance director", "managing director",
    "md", "ceo", "chief executive", "board", "director", "president",
    "vp finance", "vice president finance", "company secretary",
}


def _extract_domain(website: str) -> str:
    if not website:
        return ""
    website = website.lower().strip()
    website = re.sub(r"^https?://", "", website)
    website = re.sub(r"^www\.", "", website)
    return website.split("/")[0].strip()


def _is_exec(position: str) -> bool:
    pos = position.lower()
    return any(k in pos for k in EXEC_KEYWORDS)


async def enrich_contacts(companies: list[dict]) -> list[dict]:
    api_key = settings.hunter_api_key
    if not api_key or api_key == "your_key_here":
        for c in companies:
            c["contacts"] = []
        return companies

    async with httpx.AsyncClient(timeout=15) as client:
        for company in companies:
            domain = _extract_domain(company.get("website", ""))
            if not domain:
                company["contacts"] = []
                continue
            try:
                resp = await client.get(
                    f"{HUNTER_BASE}/domain-search",
                    params={"domain": domain, "api_key": api_key, "limit": 20},
                )
                data = resp.json().get("data", {})
                emails = data.get("emails", [])

                contacts = []
                for e in emails:
                    position = e.get("position", "") or ""
                    name = f"{e.get('first_name', '')} {e.get('last_name', '')}".strip()
                    linkedin = (
                        f"https://www.linkedin.com/search/results/people/?keywords="
                        f"{name.replace(' ', '+')}+{company['name'].replace(' ', '+')}"
                    )
                    contacts.append({
                        "name": name,
                        "email": e.get("value", ""),
                        "position": position,
                        "linkedin_url": linkedin,
                    })

                # Prioritise executives
                exec_contacts = [c for c in contacts if _is_exec(c["position"])]
                other_contacts = [c for c in contacts if not _is_exec(c["position"])]
                company["contacts"] = (exec_contacts + other_contacts)[:10]

            except Exception:
                company["contacts"] = []

    return companies
