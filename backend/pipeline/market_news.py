"""
Market news feed from NSE corporate announcements.

Fetches recent announcements and filters for signals that indicate
a company may need credit rating services: expansion, capex, fund
raising, acquisitions, NCD/bond issuance, rating actions, etc.
"""

import asyncio
import re
import time
from datetime import date, timedelta

import httpx

from backend.pipeline import news_archive, source_health

NSE_ANNOUNCEMENTS = "https://www.nseindia.com/api/corporate-announcements"
SOURCE = "NSE announcements"   # the name source_health and the UI show
NSE_WARMUP = "https://www.nseindia.com"

_WARMUP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
    "Connection": "keep-alive",
}

_client: httpx.AsyncClient | None = None
_warmed_at = 0.0


def _get_client(force_new: bool = False) -> httpx.AsyncClient:
    global _client
    if force_new and _client is not None and not _client.is_closed:
        try:
            import asyncio
            asyncio.get_event_loop().create_task(_client.aclose())
        except Exception:
            pass
        _client = None
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True)
    return _client


async def _warmup(client: httpx.AsyncClient) -> None:
    global _warmed_at
    if time.time() - _warmed_at > 120:
        try:
            r = await client.get(NSE_WARMUP, headers=_WARMUP_HEADERS)
            if r.status_code in (200, 403) and client.cookies:
                _warmed_at = time.time()
        except Exception:
            pass


_SIGNAL_KEYWORDS = {
    "fund_raise": [
        r"\bfund\s*rais", r"\bNCD\b", r"\bnon[- ]convertible\s+debenture",
        r"\bbond\s+issu", r"\bdebt\b.*\bissu", r"\bcommercial\s+paper",
        r"\bprivate\s+placement", r"\bpublic\s+issue", r"\brights\s+issue",
        r"\bQIP\b", r"\bqualified\s+institutional", r"\bwarrant",
        r"\bpreferential\s+allotment", r"\bfund\s+infusion",
    ],
    "expansion": [
        r"\bexpansion\b", r"\bnew\s+plant\b", r"\bnew\s+facility",
        r"\bcapacity\b.*\b(increase|addition|enhance|augment)",
        r"\bcapex\b", r"\bcapital\s+expenditure", r"\bgreenfield\b",
        r"\bbrownfield\b", r"\bnew\s+project\b", r"\bnew\s+unit\b",
        r"\bmanufacturing\b.*\bunit\b", r"\bsetup\b.*\bplant\b",
    ],
    "acquisition": [
        r"\bacquisition\b", r"\bacquir", r"\bmerger\b", r"\bamalgamation\b",
        r"\btakeover\b", r"\bslump\s+sale\b", r"\bbuy\b.*\bstake\b",
        r"\bstrategic\s+investment\b", r"\bjoint\s+venture\b",
    ],
    "rating_action": [
        r"\bcredit\s+rating\b",
        r"\brating\b.*\b(assign|upgrad|downgrad|withdraw|reaffirm|revis)",
        r"\bCRISIL\b", r"\bICRA\b", r"\bCARE\b.*\bRating", r"\bIndia\s+Ratings\b",
        r"\bAcuit[eé]\b", r"\bBrickwork\b", r"\bInfomerics\b",
    ],
    "board_approval": [
        r"\bboard\s+meeting\b.*\b(outcome|result)",
        r"\bboard\b.*\bapprov",
        r"\bboard\b.*\bfund\s*rais",
        r"\bboard\b.*\bNCD\b",
        r"\bboard\b.*\bborrow",
    ],
}

_compiled: dict[str, list[re.Pattern]] = {}
for cat, patterns in _SIGNAL_KEYWORDS.items():
    _compiled[cat] = [re.compile(p, re.IGNORECASE) for p in patterns]


def _classify(text: str) -> list[str]:
    categories = []
    for cat, patterns in _compiled.items():
        for pat in patterns:
            if pat.search(text):
                categories.append(cat)
                break
    return categories


_MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05",
    "Jun": "06", "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10",
    "Nov": "11", "Dec": "12",
}


def _format_date(an_dt: str) -> str:
    m = re.match(r"(\d{2})-([A-Za-z]{3})-(\d{4})", an_dt or "")
    if not m:
        return ""
    return f"{m.group(3)}-{_MONTHS.get(m.group(2).title(), '01')}-{m.group(1)}"


def _nse_date(d: date) -> str:
    return d.strftime("%d-%m-%Y")


async def _try_fetch(client: httpx.AsyncClient, from_date: date, to_date: date):
    await _warmup(client)
    r = await client.get(NSE_ANNOUNCEMENTS, params={
        "index": "equities",
        "from_date": _nse_date(from_date),
        "to_date": _nse_date(to_date),
    })
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return None, f"NSE returned HTTP {r.status_code}."
    data = r.json()
    items = data if isinstance(data, list) else data.get("data", [])
    return items, None


async def fetch_market_news(days: int = 7) -> dict:
    to_date = date.today()
    from_date = to_date - timedelta(days=days)

    # Attempt 1: existing client
    client = _get_client()
    items = None
    err_msg = ""
    try:
        items, err_msg = await _try_fetch(client, from_date, to_date)
    except Exception:
        items = None

    # Attempt 2: fresh client (new cookies)
    if items is None:
        global _warmed_at
        _warmed_at = 0.0
        client = _get_client(force_new=True)
        try:
            items, err_msg = await _try_fetch(client, from_date, to_date)
        except Exception as e:
            items = None
            err_msg = str(e) or "Connection failed"

    if items is None:
        source_health.record(SOURCE, False, error=err_msg or "no answer")
        return {
            "items": [], "status": "blocked", "source": "NSE",
            "message": f"Could not reach NSE after 2 attempts. {err_msg}",
        }

    all_items = []
    for it in items:
        desc = str(it.get("desc") or "")
        attachment_text = str(it.get("attchmntText") or "")
        blob = f"{desc} {attachment_text}"

        categories = _classify(blob)
        if not categories:
            continue

        company_name = (
            it.get("sm_name") or it.get("smName") or it.get("symbol") or ""
        ).strip()
        symbol = str(it.get("symbol") or "").strip()

        all_items.append({
            "company": company_name,
            "symbol": symbol,
            "date": _format_date(str(it.get("an_dt") or "")),
            "subject": desc[:400],
            "categories": categories,
            "attachment": str(it.get("attchmntFile") or ""),
            "link": str(it.get("attchmntFile") or ""),
            "exchange": "NSE",
            "source": "NSE",
        })

    all_items.sort(key=lambda x: x["date"], reverse=True)

    # Every poll is kept (BUILD_PLAN Phase 1): the archive is the record, this
    # response is just today's view of it.
    source_health.record(SOURCE, True, len(items))
    new = await asyncio.to_thread(news_archive.record, all_items)

    status = "ok" if all_items else "empty"
    return {
        "items": all_items[:200],
        "status": status,
        "source": "NSE Corporate Announcements",
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "total_raw": len(items),
        "total_filtered": len(all_items),
        "archived_new": new,
    }
