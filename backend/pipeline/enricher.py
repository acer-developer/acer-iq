import logging
import re

import httpx

from backend.config import settings
from backend.netutil import SourceStatus, cache, limiter

log = logging.getLogger("acer_iq.hunter")

HUNTER_BASE = "https://api.hunter.io/v2"
CACHE_TTL = 24 * 3600

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


async def enrich_contacts(companies: list[dict],
                          status: SourceStatus | None = None) -> list[dict]:
    api_key = settings.hunter_api_key
    if not api_key or api_key == "your_key_here":
        if status:
            status.skip("hunter", "no API key configured")
        for c in companies:
            c["contacts"] = []
        return companies

    async with httpx.AsyncClient(timeout=15) as client:
        for company in companies:
            domain = _extract_domain(company.get("website", ""))
            if not domain:
                company["contacts"] = []
                continue

            cache_key = "hunter:" + domain
            hit = cache.get(cache_key)
            if hit is not None:
                company["contacts"] = hit
                if status:
                    status.ok("hunter")
                continue

            try:
                async with limiter("hunter"):
                    resp = await client.get(
                        f"{HUNTER_BASE}/domain-search",
                        params={"domain": domain, "api_key": api_key, "limit": 20},
                    )
                payload = resp.json()
                if resp.status_code != 200:
                    detail = str(payload.get("errors", resp.status_code))[:200]
                    log.warning("Hunter HTTP %s for %s: %s", resp.status_code, domain, detail)
                    if status:
                        status.fail("hunter", detail)
                    company["contacts"] = []
                    continue
                if status:
                    status.ok("hunter")
                data = payload.get("data", {})
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
                cache.set_result(cache_key, company["contacts"], CACHE_TTL)

            except Exception as exc:
                log.warning("Hunter enrichment failed for %s: %r", domain, exc)
                if status:
                    status.fail("hunter", repr(exc))
                company["contacts"] = []

    return companies
