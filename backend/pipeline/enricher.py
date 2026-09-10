import logging
import re

import httpx

from backend.config import settings

log = logging.getLogger("acer-iq.contacts")

HUNTER_BASE = "https://api.hunter.io/v2"
# Hunter's free tier is 25 domain searches per MONTH. One unlucky search over
# companies that all have websites would spend the whole allowance, so cap it
# and say so in the log rather than discovering the quota is gone next week.
MAX_HUNTER_LOOKUPS = 5

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

    used = 0
    async with httpx.AsyncClient(timeout=15) as client:
        for company in companies:
            company.setdefault("contacts", [])
            domain = _extract_domain(company.get("website", ""))
            if not domain:
                continue
            if used >= MAX_HUNTER_LOOKUPS:
                log.info("Hunter lookup cap (%d) reached - skipping contacts for "
                         "the remaining leads to protect the monthly quota",
                         MAX_HUNTER_LOOKUPS)
                break
            used += 1
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

            except Exception as e:
                log.warning("Hunter lookup failed for %s: %s: %s",
                            domain, type(e).__name__, e)

    return companies
