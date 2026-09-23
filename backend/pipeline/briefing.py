"""
BRIEFING - news for the companies already in the pipeline.

ROADMAP_V3 phase 3. This is deliberately NOT a market feed: a global news wall
is something nobody reads twice. It answers one question, asked right before a
call - "what has been written about this company lately" - by matching the
saved-lead names against the RSS publishers in `rss_news.py`.

It does not rank the pipeline and must not be read as a signal: an issuer with
no coverage is usually just a mid-cap nobody wrote about this week, not a quiet
one. `scanned` and `sources_fail` are returned so an empty briefing is legible
as "we read 195 items and none named you" rather than "the feeds were down".

MATCH CONFIDENCE IS PART OF THE OUTPUT, on purpose. Two bad matches in a demo
and BD stops trusting every number in the tool, so a single-token name ("Navi",
"Tata") is labelled `weak` rather than silently presented as this company's
news. Names shorter than four characters are not matched at all.

Self-check (no network):  python -m backend.pipeline.briefing
"""
from __future__ import annotations

import logging
import re

from backend.pipeline.pipeline_store import norm_name
from backend.pipeline.rss_news import fetch_rss_news

log = logging.getLogger(__name__)

# Words that carry no identity on their own. A core name that reduces to these
# would match half the business press.
_STOPWORDS = {"FINANCE", "FINANCIAL", "CAPITAL", "INDIA", "INDIAN", "GROUP",
              "HOLDINGS", "ENTERPRISES", "INDUSTRIES", "SERVICES", "COMPANY",
              "CORPORATION", "BANK", "NATIONAL", "GENERAL", "UNION", "POWER"}

MIN_CORE_CHARS = 4


def _core_tokens(company_name: str) -> list[str]:
    """The identifying tokens of a company name, suffixes and filler removed."""
    core = norm_name(company_name)
    tokens = [t for t in re.split(r"[^A-Z0-9&]+", core) if t]
    distinctive = [t for t in tokens if t not in _STOPWORDS and len(t) > 1]
    return distinctive or tokens


def match_items(company_name: str, items: list[dict]) -> list[dict]:
    """Every news item naming this company, each tagged with match confidence.

    Pure, so the self-check exercises the real matcher rather than a stub."""
    tokens = _core_tokens(company_name)
    if not tokens or len("".join(tokens)) < MIN_CORE_CHARS:
        return []

    # The strong match is the first two distinctive tokens together - not the
    # whole legal name, which no newsroom ever prints ("Satin Creditcare", never
    # "Satin Creditcare Network Limited"). A single leading token is the weak
    # match: reported and labelled, not dropped and not trusted.
    phrase = re.compile(r"\b" + r"\s+".join(re.escape(t) for t in tokens[:2]) + r"\b",
                        re.IGNORECASE)
    lead = re.compile(r"\b" + re.escape(tokens[0]) + r"\b", re.IGNORECASE)
    weak_ok = len(tokens[0]) >= MIN_CORE_CHARS and tokens[0] not in _STOPWORDS

    out = []
    for it in items:
        blob = f"{it.get('subject', '')} {it.get('description', '')}"
        if phrase.search(blob):
            confidence = "exact"
        elif weak_ok and len(tokens) > 1 and lead.search(blob):
            confidence = "weak"
        else:
            continue
        out.append({
            "date": it.get("date", ""),
            "subject": it.get("subject", ""),
            "link": it.get("link", ""),
            "source": it.get("source", ""),
            "categories": it.get("categories", []),
            "confidence": confidence,
        })
    out.sort(key=lambda x: (x["confidence"] != "exact", x["date"]), reverse=False)
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


async def build_briefing(leads: list[dict], limit_per_lead: int = 8) -> dict:
    """News per saved lead, in one feed fetch for the whole pipeline.

    One fetch, not one per company: the publishers are the same for every lead,
    so a per-lead fetch would be the same five HTTP calls repeated N times."""
    news = await fetch_rss_news()
    items = news.get("all_items", [])

    briefs = []
    for lead in leads:
        name = lead.get("company_name", "")
        matched = match_items(name, items)
        briefs.append({
            "company_name": name,
            "stage": lead.get("stage", ""),
            "winnability": lead.get("winnability"),
            "items": matched[:limit_per_lead],
            "total": len(matched),
        })

    briefs.sort(key=lambda b: (-b["total"], b["company_name"]))
    return {
        "briefs": briefs,
        "with_news": sum(1 for b in briefs if b["total"]),
        "scanned": len(items),
        "sources_ok": news.get("sources_ok", []),
        "sources_fail": news.get("sources_fail", []),
        # An empty briefing with no live source is a different fact from an
        # empty briefing after reading five papers. The UI has to say which.
        "status": news.get("status", "blocked"),
    }


# -- self-check (no network) -------------------------------------------------

def _demo() -> None:
    items = [
        {"subject": "Satin Creditcare raises Rs 300 crore via NCDs",
         "description": "The microfinance lender said", "date": "2026-09-14",
         "link": "x", "source": "BusinessLine", "categories": ["fund_raise"]},
        {"subject": "Navigating the bond market in a rate-cut year",
         "description": "analysts say", "date": "2026-09-13", "link": "y",
         "source": "Moneycontrol", "categories": []},
        {"subject": "Navi Finserv gets board nod for Rs 500 crore raise",
         "description": "", "date": "2026-09-12", "link": "z",
         "source": "Business Standard", "categories": ["board_approval"]},
        {"subject": "Satin sees margin pressure", "description": "",
         "date": "2026-09-11", "link": "w", "source": "LiveMint", "categories": []},
    ]

    hits = match_items("Satin Creditcare Network Limited", items)
    # The full phrase match, plus "Satin" alone as a weak one - labelled, not hidden.
    assert [h["confidence"] for h in hits] == ["exact", "weak"], hits
    assert hits[0]["date"] == "2026-09-14"

    # "Navigating" must not count as Navi: word boundaries, not substrings.
    navi = match_items("Navi Finserv Limited", items)
    assert len(navi) == 1 and navi[0]["confidence"] == "exact", navi

    # A name that reduces to filler matches nothing rather than everything.
    assert match_items("India Finance Limited", items) == []
    assert match_items("ABC Ltd", items) == []

    # Suffix variants are one company, same as the pipeline's de-duplication.
    assert match_items("Navi Finserv Pvt Ltd", items) == navi

    print("briefing self-check: ok")


if __name__ == "__main__":
    _demo()
