"""
LEAD QUEUE - the ranked list the dashboard renders.

One batch pass, not a per-company fan-out: `cra_press.fetch_recent_actions`
returns every recent rating action in a couple of HTTP calls, we group those by
issuer, and score each issuer with `winnability.score`. No network per lead, so
the whole queue costs the same as one page fetch.

WHAT THIS QUEUE CAN AND CANNOT SEE - read before trusting a number:

  * Three agencies carry a recent-actions feed and are read here: ACUITE,
    BRICKWORK and INDRA. CARE has no feed but answers per-company lookups, so it
    joins via the enrichment pass below. CRISIL, ICRA and INFOMERICS cannot be
    read at all over plain HTTP (see cra_press).
  * **`first_timer` can never fire here**, by construction: a company only
    reaches this queue by having a published rating action, so a genuinely
    unrated first-time issuer is by definition absent. First-timers have to come
    from a registry-minus-rated-universe join once the MCA master lands
    (ROADMAP_V3 phase 4) - not from here.
  * **`multi_cra` cannot be seen in the feeds either.** Measured on live data,
    cross-feed overlap was exactly zero: each agency publishes about a different
    set of companies, so two feeds practically never name the same issuer in the
    same window. Coverage is a per-company question, which is why the top slice
    of the queue gets an explicit CARE lookup (`_enrich_coverage`).

Every response therefore carries `coverage`, so the UI can say which agencies
were actually read. A queue that silently looks complete is the failure mode
this whole product is trying to avoid.

Self-check (no network):  python -m backend.pipeline.lead_queue
"""
from __future__ import annotations

import asyncio
import logging

from backend.pipeline import action_history, cra_press, winnability

log = logging.getLogger(__name__)

def _norm(name: str) -> str:
    """Fold an issuer name for grouping. Deliberately conservative: we would
    rather split one company into two rows than merge two companies into one and
    put the wrong rating against a name a salesperson is about to call."""
    out = " ".join((name or "").upper().split())
    for suffix in (" PRIVATE LIMITED", " PVT LTD", " PVT. LTD.", " LIMITED", " LTD.", " LTD"):
        if out.endswith(suffix):
            out = out[: -len(suffix)]
            break
    return out.strip(" .,-")


def _credit_data_for(actions: list[dict]) -> dict:
    """Shape one issuer's actions into what `winnability.score` reads.

    `data_status` is "ok" by construction: we are holding published actions for
    this issuer, so the sources demonstrably answered. Never fabricate "ok" for
    an issuer we merely failed to find."""
    agencies = sorted({a.get("agency", "") for a in actions if a.get("agency")})
    return {
        "rating_actions": actions,
        "agencies": [],
        "rated_by_count": len(agencies),
        "data_status": "ok",
        "total_instruments": len(actions),
    }


def _latest(actions: list[dict]) -> dict:
    return max(actions, key=lambda a: cra_press._date_key(a.get("date", "")))


def build_rows(actions: list[dict]) -> list[dict]:
    """Group actions by issuer and score each one. Pure - no I/O, so this is
    what the self-check exercises."""
    grouped: dict[str, list[dict]] = {}
    for act in actions:
        key = _norm(act.get("company_name", ""))
        if key:
            grouped.setdefault(key, []).append(act)

    rows = []
    for key, acts in grouped.items():
        credit_data = _credit_data_for(acts)
        verdict = winnability.score({"name": key}, credit_data)
        newest = _latest(acts)
        rows.append({
            "company_name": newest.get("company_name") or key,
            "agencies_seen": sorted({a.get("agency", "") for a in acts if a.get("agency")}),
            "latest_rating": newest.get("rating", ""),
            "latest_action": newest.get("action", ""),
            "latest_date": newest.get("date", ""),
            "source_url": newest.get("source_url", ""),
            "action_count": len(acts),
            # kept only until enrichment has rescored; stripped before returning
            "_actions": acts,
            **verdict,
        })

    # Blocked leads sort last regardless of score: they are not callable yet, so
    # they must never sit at the top of a queue meant to be worked top-down.
    rows.sort(key=lambda r: (r["blocked"], -r["winnability"], r["company_name"]))
    return rows


async def _enrich_coverage(rows: list[dict], limit: int) -> int:
    """Ask CARE what it rates, for the top `limit` candidates, and rescore them.

    WHY THIS EXISTS: `multi_cra` - already rated by two or more agencies, our
    single best "will take a third quote" signal - is undetectable from the
    recent-action feeds. Each agency's feed lists different companies, so two
    feeds almost never name the same issuer in the same window; measured on live
    data, cross-feed overlap was exactly zero. Coverage is a per-company
    question and has to be asked per company.

    Only the top slice is enriched, because this is the one place in the queue
    that costs network per lead. Blocked rows are skipped - they are not
    callable whatever their coverage turns out to be.

    Returns how many rows were actually enriched, so `coverage` can say so.
    """
    candidates = [r for r in rows if not r["blocked"]][:limit]
    if not candidates:
        return 0

    sem = asyncio.Semaphore(4)          # be a polite guest on CARE's search box

    async def one(row: dict) -> None:
        async with sem:
            care = await cra_press.fetch_care_for_company(row["company_name"])
        if not care:
            return
        row["agencies_seen"] = sorted(set(row["agencies_seen"]) | {"CARE"})
        row["care_ratings"] = [c["rating"] for c in care][:4]
        # Rescore against the fuller picture. The credit screen re-runs too, so
        # a CARE rating in default can newly block a lead that looked fine.
        merged = _credit_data_for(row["_actions"] + care)
        row.update(winnability.score({"name": row["company_name"]}, merged))

    await asyncio.gather(*(one(r) for r in candidates), return_exceptions=True)
    return len(candidates)


async def build_queue(days: int = 30, enrich: int = 15) -> dict:
    """The ranked queue. Never raises on a source failure - it degrades and says
    so in `coverage`."""
    # No cache here on purpose: cra_press already TTL-caches the fetch, and
    # regrouping ~120 rows costs microseconds. A second layer would only add a
    # staleness window with no saving.
    data = await cra_press.fetch_recent_actions(days=days)

    # Fold today's fetch into the archive, then build from live + archived. The
    # feeds are latest-page snapshots, so without this the `days` window was
    # decoration: days=365 returned the same rows as days=60. History now
    # accumulates from ordinary use, with no scheduled job to forget to run.
    new_rows = action_history.record(data["actions"])
    actions = action_history.merge(data["actions"], action_history.since(days))
    rows = build_rows(actions)
    enriched = await _enrich_coverage(rows, enrich)
    # Enrichment can change scores and block states, so the order is only valid
    # after it has run.
    rows.sort(key=lambda r: (r["blocked"], -r["winnability"], r["company_name"]))
    for r in rows:
        r.pop("_actions", None)

    sources = data["sources"]
    readable = [a for a, s in sources.items() if s == "ok"]
    if not readable:
        log.warning("lead_queue: no CRA source returned data (sources=%s)", sources)

    result = {
        "leads": rows,
        "total": len(rows),
        "workable": sum(1 for r in rows if not r["blocked"] and not r["suppressed"]),
        "blocked": sum(1 for r in rows if r["blocked"]),
        "window_days": days,
        "coverage": {
            "sources": sources,
            "agencies_read": readable,
            "agencies_total": len(sources),
            "data_status": data["data_status"],
            "enriched": enriched,
            "history": action_history.stats() | {"new_this_build": new_rows},
            # Said in words so the UI cannot quietly drop it.
            "note": ("Built from "
                     + (", ".join(readable) if readable else "no agency")
                     + f" of {len(sources)} CRAs; top {enriched} checked against "
                       "CARE for full coverage. The window is served from the "
                       "local archive, which only goes back as far as this tool "
                       "has been running. First-time issuers cannot appear here "
                       "by construction; agencies that could not be read are "
                       "listed as blocked."),
        },
    }
    return result


# ── self-check (no network) ─────────────────────────────────────────────────

def _demo() -> None:
    actions = [
        {"agency": "ACUITE", "company_name": "Spectron Engineers Private Limited",
         "rating": "ACUITE BBB Stable", "action": "Reaffirmed", "date": "09-09-2026",
         "isin": "", "source_url": "https://example/1"},
        {"agency": "BRICKWORK", "company_name": "SPECTRON ENGINEERS PVT LTD",
         "rating": "BWR BBB-", "action": "Withdrawn at the issuer's request",
         "date": "01-09-2026", "isin": "", "source_url": "https://example/2"},
        {"agency": "ACUITE", "company_name": "Atithi Paper LLP",
         "rating": "ACUITE D", "action": "Issuer not cooperating",
         "date": "05-09-2026", "isin": "", "source_url": "https://example/3"},
        {"agency": "ACUITE", "company_name": "Steady Corp Limited",
         "rating": "ACUITE AA", "action": "Reaffirmed", "date": "04-09-2026",
         "isin": "", "source_url": "https://example/4"},
    ]
    rows = build_rows(actions)

    spectron = next(r for r in rows if "SPECTRON" in r["company_name"].upper())
    # Naming variants of one issuer must collapse into a single lead, or the
    # same company gets called twice by two people.
    assert spectron["action_count"] == 2, rows
    assert spectron["agencies_seen"] == ["ACUITE", "BRICKWORK"], spectron
    assert spectron["flags"]["multi_cra"] and spectron["flags"]["self_withdrawn"], spectron

    atithi = next(r for r in rows if "Atithi" in r["company_name"])
    assert atithi["flags"]["inc_tagged"], atithi
    assert atithi["blocked"], "a defaulted issuer must not be callable"

    steady = next(r for r in rows if "Steady" in r["company_name"])
    assert steady["winnability"] == 0 and steady["suppressed"], steady

    # Blocked rows sink to the bottom no matter how they scored.
    assert rows[-1]["blocked"] is True, [r["company_name"] for r in rows]
    # Nothing in this queue can ever be a first-time issuer.
    assert not any(r["flags"]["first_timer"] for r in rows)

    assert build_rows([]) == []
    print("lead_queue self-check: ok")


if __name__ == "__main__":
    _demo()
