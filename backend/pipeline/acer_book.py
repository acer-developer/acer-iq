"""
ACER BOOK - ACER's own ratings, as the 8th agency.

Two jobs, and the first one is the point:

  1. STOP PITCHING OUR OWN CLIENTS. Nothing else in this codebase knows who ACER
     already rates, so Viviana Power Tech could surface in the ranked queue as a
     lead and someone could cold-call an existing client. That is the most
     embarrassing failure this tool can produce, and it needs no new data source
     to prevent - only this list.
  2. SURFACE RENEWALS. Ratings are surveilled annually, so a rating assigned last
     June is a renewal conversation next June. That is the cheapest revenue in
     the business, and nobody was tracking it.

WHY A JSON FILE RATHER THAN A SCRAPER. acerratings.com/media-press returns 403 to
plain HTTP (its WAF blocks non-browser clients), so our own site is the one CRA
site we cannot read programmatically. With three actions across two issuers,
a hand-maintained file is smaller, clearer and more reliable than the browser
infrastructure scraping it would need. Update backend/data/acer_book.json when
ACER publishes; `staleness()` reports how long since it was touched, so a
forgotten file is visible rather than silently wrong.

Self-check (no network):  python -m backend.pipeline.acer_book
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("acer-iq.acer_book")

BOOK_PATH = Path(__file__).parent.parent / "data" / "acer_book.json"

# SEBI-registered CRAs surveil annually, so a rating action dates the next
# review a year out unless the rationale says otherwise.
SURVEILLANCE_MONTHS = 12

# How long before a surveillance date counts as "coming up" and worth a call.
RENEWAL_LEAD_DAYS = 90


def _norm(name: str) -> str:
    """Same folding as lead_queue._norm, so a name matches across sources."""
    out = " ".join((name or "").upper().split())
    for suffix in (" PRIVATE LIMITED", " PVT LTD", " PVT. LTD.", " LIMITED", " LTD.", " LTD"):
        if out.endswith(suffix):
            out = out[: -len(suffix)]
            break
    return out.strip(" .,-")


def _load() -> dict:
    try:
        return json.loads(BOOK_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("acer_book.json missing - ACER's own clients will not be "
                    "recognised, so they can surface as leads")
        return {"actions": []}
    except Exception as e:
        log.error("acer_book.json unreadable: %s: %s", type(e).__name__, e)
        return {"actions": []}


def actions() -> list[dict]:
    """ACER's rating actions, shaped like every other agency's in this codebase
    so credit_history can merge them without translation."""
    out = []
    for a in _load().get("actions", []):
        out.append({
            "agency": "ACER",
            "company_name": a.get("company_name", ""),
            "rating": a.get("rating", ""),
            "action": a.get("action", ""),
            "date": a.get("date", ""),
            "isin": (a.get("isins") or [""])[0],
            "source_url": a.get("source_url", ""),
            "instrument_type": a.get("instrument_type", ""),
            "amount_crores": a.get("amount_crores", ""),
        })
    return out


def client_names() -> set[str]:
    """Normalised names of every issuer ACER rates. The queue filters on this."""
    return {_norm(a["company_name"]) for a in actions() if a.get("company_name")}


def is_client(company_name: str) -> bool:
    return _norm(company_name) in client_names()


def _parse(d: str):
    try:
        return datetime.strptime(d, "%d-%m-%Y")
    except (ValueError, TypeError):
        return None


def renewals(within_days: int = RENEWAL_LEAD_DAYS) -> list[dict]:
    """Existing clients whose surveillance falls due inside the window.

    Only the LATEST action per issuer counts: Viviana was assigned in March and
    reaffirmed in August, and the reaffirmation is what dates the next review.
    Using the older one would raise a renewal alarm five months early."""
    latest: dict[str, dict] = {}
    for a in actions():
        key = _norm(a["company_name"])
        d = _parse(a["date"])
        if not key or d is None:
            continue
        prev = latest.get(key)
        if prev is None or d > prev["_dt"]:
            latest[key] = {**a, "_dt": d}

    today = datetime.now()
    out = []
    for rec in latest.values():
        due = rec["_dt"] + timedelta(days=SURVEILLANCE_MONTHS * 30.44)
        days_out = (due - today).days
        if days_out <= within_days:
            out.append({
                "company_name": rec["company_name"],
                "rating": rec["rating"],
                "last_action": rec["action"],
                "last_action_date": rec["date"],
                "surveillance_due": due.strftime("%d-%m-%Y"),
                "days_until_due": days_out,
                "overdue": days_out < 0,
                "source_url": rec["source_url"],
            })
    out.sort(key=lambda r: r["days_until_due"])
    return out


def staleness() -> dict:
    """How current the file is. A hand-maintained list that nobody updates is
    worse than no list, because it quietly stops recognising new clients."""
    meta = _load()
    updated = meta.get("_last_updated", "")
    try:
        age = (datetime.now() - datetime.strptime(updated, "%Y-%m-%d")).days
    except (ValueError, TypeError):
        age = None
    return {
        "last_updated": updated or "unknown",
        "days_old": age,
        "clients": len(client_names()),
        "actions": len(actions()),
        "stale": age is not None and age > 90,
        "source": meta.get("_source", ""),
    }


def _demo() -> None:
    acts = actions()
    assert len(acts) == 3, acts
    assert all(a["agency"] == "ACER" for a in acts)

    # The whole point: our own clients must be recognisable, under any spelling.
    assert is_client("Viviana Power Tech Limited")
    assert is_client("VIVIANA POWER TECH LTD"), "suffix folding must match"
    assert is_client("Finstars Capital Limited")
    assert not is_client("Some Other Company Limited")
    assert client_names() == {"VIVIANA POWER TECH", "FINSTARS CAPITAL"}, client_names()

    # Viviana has two actions; only the newer one may date the next review, or
    # the renewal alarm fires five months early.
    rs = renewals(within_days=10_000)
    viviana = next(r for r in rs if "Viviana" in r["company_name"])
    assert viviana["last_action_date"] == "24-08-2026", viviana
    assert viviana["last_action"] == "Reaffirmed", viviana
    assert len(rs) == 2, rs                      # two issuers, not three actions

    # A tight window should not sweep in a review that is a year out.
    assert renewals(within_days=1) == []

    s = staleness()
    assert s["clients"] == 2 and s["actions"] == 3, s

    print("acer_book self-check: ok")


if __name__ == "__main__":
    _demo()
