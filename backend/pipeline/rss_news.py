"""
Financial news from RSS feeds: Economic Times Markets + LiveMint Markets.

Fetches, parses, and classifies news items using the same signal-keyword
engine as NSE corporate announcements.
"""

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from backend.pipeline import news_archive, source_health

_FEEDS = {
    "Economic Times": {
        "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "site": "https://economictimes.indiatimes.com/markets",
    },
    "LiveMint": {
        "url": "https://www.livemint.com/rss/markets",
        "site": "https://www.livemint.com/market",
    },
    # Added for the briefing surface: company news for names already in the
    # pipeline. Three more publishers is three more chances that a company we
    # are about to call appears at all - one feed's markets page is mostly
    # index commentary, and a mid-cap issuer shows up in none of it.
    "BusinessLine": {
        "url": "https://www.thehindubusinessline.com/companies/feeder/default.rss",
        "site": "https://www.thehindubusinessline.com/companies/",
    },
    "Moneycontrol": {
        "url": "https://www.moneycontrol.com/rss/business.xml",
        "site": "https://www.moneycontrol.com/news/business/",
    },
    "Business Standard": {
        "url": "https://www.business-standard.com/rss/companies-101.rss",
        "site": "https://www.business-standard.com/companies",
    },
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/xml, text/xml, application/rss+xml, */*",
    "Accept-Encoding": "gzip, deflate",
}

_SIGNAL_KEYWORDS = {
    "fund_raise": [
        r"\bfund\s*rais", r"\bNCD\b", r"\bnon[- ]convertible\s+debenture",
        r"\bbond\s+issu", r"\bdebt\b.*\bissu", r"\bcommercial\s+paper",
        r"\bprivate\s+placement", r"\brights\s+issue", r"\bIPO\b",
        r"\bQIP\b", r"\bqualified\s+institutional", r"\bwarrant",
        r"\bpreferential\s+allotment", r"\bfund\s+infusion",
        r"\bOFS\b", r"\boffer\s+for\s+sale",
    ],
    "expansion": [
        r"\bexpansion\b", r"\bnew\s+plant\b", r"\bnew\s+facility",
        r"\bcapacity\b.*\b(increase|addition|enhance|augment)",
        r"\bcapex\b", r"\bcapital\s+expenditure", r"\bgreenfield\b",
        r"\bbrownfield\b", r"\bnew\s+project\b",
    ],
    "acquisition": [
        r"\bacquisition\b", r"\bacquir", r"\bmerger\b", r"\bamalgamation\b",
        r"\btakeover\b", r"\bslump\s+sale\b", r"\bbuy\b.*\bstake\b",
        r"\bstrategic\s+investment\b", r"\bjoint\s+venture\b",
    ],
    "rating_action": [
        r"\bcredit\s+rating\b",
        r"\brating\b.*\b(assign|upgrad|downgrad|withdraw|reaffirm|revis|cut|affirm)",
        r"\bCRISIL\b", r"\bICRA\b", r"\bCARE\b.*\b[Rr]ating",
        r"\bIndia\s+Ratings\b", r"\bAcuit[eé]\b", r"\bBrickwork\b",
        r"\bInfomerics\b", r"\bMoody", r"\bS&P\b", r"\bFitch\b",
    ],
    "board_approval": [
        r"\bboard\s+meeting\b.*\b(outcome|result)",
        r"\bboard\b.*\bapprov",
        r"\bboard\b.*\bfund\s*rais",
        r"\bboard\b.*\bborrow",
    ],
    "market_move": [
        r"\bshare\s+price\b.*\b(jump|surge|rally|crash|tank|fall|drop|slip|soar|plunge)",
        r"\bstock\b.*\b(surge|rally|crash|tank|fall|drop|soar|plunge|hits?\s+high|hits?\s+low)",
        r"\b(52[- ]week|all[- ]time)\s+(high|low)\b",
        r"\bmarket\s+cap\b",
    ],
    "results": [
        r"\bQ[1-4]\s+(result|earning|profit|revenue|net\s+profit)",
        r"\b(quarterly|annual)\s+(result|earning|profit|revenue)",
        r"\bnet\s+profit\b.*\b(rise|fall|jump|drop|surge|decline|grow)",
        r"\brevenue\b.*\b(rise|fall|jump|grow|surge|decline)",
        r"\bPAT\b.*\b(rise|fall|jump|grow)",
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


def _parse_pub_date(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%a, %d %b %Y %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _strip_cdata(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<!\[CDATA\[", "", text)
    text = re.sub(r"\]\]>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    import html
    text = html.unescape(text)
    return text.strip()


FEED_NAMES = list(_FEEDS)


def _parse_feed(xml_bytes: bytes, source_name: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return items

    for item_el in root.iter("item"):
        title = _strip_cdata(item_el.findtext("title") or "")
        desc = _strip_cdata(item_el.findtext("description") or "")
        link = _strip_cdata(item_el.findtext("link") or "")
        pub_date = _strip_cdata(item_el.findtext("pubDate") or "")

        blob = f"{title} {desc}"
        categories = _classify(blob)

        items.append({
            "company": "",
            "symbol": "",
            "date": _parse_pub_date(pub_date),
            "subject": title[:400],
            "description": desc[:300],
            "categories": categories,
            "link": link,
            "source": source_name,
            "exchange": "",
        })

    return items


async def fetch_rss_news() -> dict:
    all_items = []
    sources_ok = []
    sources_fail = []

    async with httpx.AsyncClient(timeout=15, headers=_HEADERS, follow_redirects=True) as client:
        for name, feed in _FEEDS.items():
            try:
                r = await client.get(feed["url"])
                if r.status_code == 200:
                    parsed = _parse_feed(r.content, name)
                    all_items.extend(parsed)
                    sources_ok.append(name)
                    source_health.record(name, True, len(parsed))
                else:
                    sources_fail.append(f"{name} (HTTP {r.status_code})")
                    source_health.record(name, False, error=f"HTTP {r.status_code}")
            except Exception as e:
                sources_fail.append(f"{name} ({e})")
                source_health.record(name, False, error=f"{type(e).__name__}: {e}")

    # Every poll is kept (BUILD_PLAN Phase 1), general items included: what
    # counts as "major" is decided on read, so the rules can change later
    # without losing what was read before the change.
    new = await asyncio.to_thread(news_archive.record, all_items)

    all_items.sort(key=lambda x: x["date"], reverse=True)

    signal_items = [it for it in all_items if it["categories"]]
    general_items = [it for it in all_items if not it["categories"]]

    return {
        "signal_items": signal_items,
        "general_items": general_items,
        "all_items": all_items,
        "status": "ok" if sources_ok else "blocked",
        "sources_ok": sources_ok,
        "sources_fail": sources_fail,
        "total_items": len(all_items),
        "total_signals": len(signal_items),
        "archived_new": new,
    }
