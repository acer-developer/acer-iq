"""
RENEWAL CALENDAR - when each rated issuer is next due for surveillance.

ROADMAP_V3 phase 2 calls this "a warm list built once and mined forever", and
TODO.md specifies a "surveillance-date extractor from rating rationale PDFs".

WHAT THE PDFS ACTUALLY CONTAIN, having read them. There is no next-surveillance
date in a rating rationale. Probed against live Brickwork rationales: the only
occurrence of the word "surveillance" is the boilerplate disclaimer ("Ratings
are subject to continuous surveillance and may be revised..."), and there is no
validity, expiry or next-review field anywhere in the document. India Ratings'
press-release pages render client-side and carry 78 characters of extractable
text, so they are unreadable without a headless browser, which ROADMAP_V3
rules out. No amount of LLM extraction can read a date that is not written.

WHAT IS THERE, AND IS BETTER. The rationale's facilities table is headed
`Previous (19-August-2025)` - the date of the issuer's *previous* rating
action. Paired with the current action's date that yields the issuer's own
observed review interval, which beats any single published date because it is
measured rather than assumed. SEBI requires a review at least annually, so
where no previous date is available a 12-month cycle is the floor.

THIS IS THEREFORE A PREDICTION, NOT A PUBLISHED DATE, and every row says so in
`basis`. Handing BD a computed date that looks published is exactly the kind of
false precision this codebase keeps refusing to ship - so a row derived from a
measured interval and one derived from the annual default must never look alike.

The calendar itself needs no network: it runs off rating action dates already
in the snapshot archive. The PDF pass is optional enrichment that sharpens the
interval and adds the rated amount, and the module degrades cleanly without it.

Self-check (no network):  python -m backend.pipeline.renewal
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta

log = logging.getLogger("acer-iq.renewal")

# SEBI requires rated instruments to be reviewed at least once a year, so this
# is a floor rather than a guess when an issuer's own interval is unknown.
DEFAULT_INTERVAL_DAYS = 365

# An issuer whose observed interval is wild - a correction, a re-rating after a
# gap - would otherwise project a due date years out and silently drop off the
# calendar. Clamp to a plausible surveillance band.
MIN_INTERVAL_DAYS = 90
MAX_INTERVAL_DAYS = 550

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# A parenthesised date anywhere in the facilities-table header, e.g.
# `(19-August-2025)`. Deliberately NOT anchored to the word "Previous": the
# header wraps across lines in the extracted text, so the live Brickwork
# rationale reads `Previous Present (19-August-2025) Present Regulator` and a
# `Previous\s*\(` anchor matches nothing at all. The header region carries
# exactly one real date, so finding it by shape is both simpler and sturdier.
_PAREN_DATE_RE = re.compile(
    r"\(\s*(\d{1,2})\s*[-\s]\s*([A-Za-z]{3,9})\s*[-\s]\s*(\d{4})\s*\)")

# How far into the document the table header can be. Past this it is narrative.
_HEADER_CHARS = 1500

# `Rs. 125.00 crores` / `Rs.125 Crs`
_AMOUNT_RE = re.compile(
    r"(?:Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)\s*(?:cr(?:o?re?s?)?|crs?)\b", re.I)

# Everything from here on is forward-looking narrative, not the rated book. The
# rating-sensitivity section quotes revenue and leverage thresholds in crore -
# the live Gulzar rationale says "Rs. 500 Crs" as an upgrade trigger against a
# rated book of Rs 125 Crs - so reading amounts past this point silently
# quadruples the issuer's size.
_NARRATIVE_RE = re.compile(r"RATING\s+SENSITIVIT|KEY\s+RATING\s+DRIVER", re.I)


def _parse_month(name: str) -> int | None:
    return _MONTHS.get(name[:3].lower())


def parse_previous_action_date(text: str) -> str:
    """The `Previous (...)` header date from a rationale, as DD-MM-YYYY.

    Returns "" rather than guessing - a wrong previous date silently corrupts
    the interval for that issuer, which is worse than falling back to annual."""
    for m in _PAREN_DATE_RE.finditer((text or "")[:_HEADER_CHARS]):
        month = _parse_month(m.group(2))
        if not month:
            continue
        try:
            d = datetime(int(m.group(3)), month, int(m.group(1)))
        except ValueError:
            continue
        return d.strftime("%d-%m-%Y")
    return ""


def parse_rated_amount(text: str) -> float | None:
    """Total rated debt in Rs crore, read from the document's summary region.

    Sizing, free, from a document already being fetched - `fit_analyzer` today
    scores on entity type and instrument count with no financials at all, so a
    Rs 5,000 cr borrower and a Rs 50 cr one are indistinguishable to it.

    The FIRST figure is taken, from the summary region only. A rationale opens
    with "...ratings for the Bank Loan Facilities of Rs. 125.00 crores", which
    is the rated book. Taking the largest instead - the obvious first guess -
    reads Rs 500 Crs off the rating-sensitivity narrative on the live Gulzar
    rationale, overstating a Rs 125 Cr issuer four-fold."""
    body = text or ""
    cut = _NARRATIVE_RE.search(body)
    if cut:
        body = body[:cut.start()]

    m = _AMOUNT_RE.search(body)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _to_date(s: str) -> datetime | None:
    try:
        return datetime.strptime((s or "").strip(), "%d-%m-%Y")
    except ValueError:
        return None


def predict_next_review(last_action_date: str, previous_action_date: str = "",
                        today: datetime | None = None) -> dict:
    """When this issuer is next likely to come up for surveillance.

    `basis` is part of the answer, not decoration: "observed" means the interval
    was measured from this issuer's own two most recent actions, "annual" means
    it is the regulatory floor applied because nothing better was known.
    """
    today = today or datetime.now()
    last = _to_date(last_action_date)
    if last is None:
        return {"due_date": "", "days_until": None, "interval_days": None,
                "basis": "unknown", "urgency": "unknown",
                "note": "no usable last-action date"}

    prev = _to_date(previous_action_date)
    interval = DEFAULT_INTERVAL_DAYS
    basis = "annual"
    if prev is not None and prev < last:
        measured = (last - prev).days
        if MIN_INTERVAL_DAYS <= measured <= MAX_INTERVAL_DAYS:
            interval, basis = measured, "observed"
        else:
            # Out-of-band gaps are real but not a surveillance cycle; say so
            # rather than quietly projecting off them.
            basis = "annual (observed interval out of band)"

    due = last + timedelta(days=interval)
    days_until = (due.date() - today.date()).days

    # The bands are what BD actually acts on. "Due" is deliberately generous on
    # the past side: a rating a fortnight overdue is a live conversation, not a
    # missed one, because the issuer is mid-review right now.
    if days_until < -60:
        urgency = "stale"
    elif days_until <= 30:
        urgency = "due"
    elif days_until <= 90:
        urgency = "approaching"
    else:
        urgency = "later"

    return {
        "due_date": due.strftime("%d-%m-%Y"),
        "days_until": days_until,
        "interval_days": interval,
        "basis": basis,
        "urgency": urgency,
        "note": ("interval measured from this issuer's own previous action"
                 if basis == "observed"
                 else "SEBI requires review at least annually; no previous "
                      "action date available, so 365 days is a floor"),
    }


def build_calendar(actions: list[dict], previous_dates: dict | None = None,
                   amounts: dict | None = None,
                   today: datetime | None = None) -> list[dict]:
    """One row per issuer, soonest due first.

    Grouped by issuer, not by instrument: a company with nine facilities is one
    phone call, and nine calendar rows for it would bury every other issuer.
    """
    previous_dates = previous_dates or {}
    amounts = amounts or {}

    by_company: dict[str, list[dict]] = {}
    for a in actions:
        name = (a.get("company_name") or "").strip()
        if not name:
            continue
        by_company.setdefault(name, []).append(a)

    rows = []
    for name, acts in by_company.items():
        dated = [x for x in acts if _to_date(x.get("date", ""))]
        if not dated:
            continue
        latest = max(dated, key=lambda x: _to_date(x["date"]))

        # An issuer's own earlier action in the archive is a measured interval
        # too, and costs no fetch - prefer the PDF's date only when we have it.
        prev_from_archive = ""
        others = [x for x in dated if _to_date(x["date"]) < _to_date(latest["date"])]
        if others:
            prev_from_archive = max(others, key=lambda x: _to_date(x["date"]))["date"]

        pred = predict_next_review(
            latest["date"], previous_dates.get(name) or prev_from_archive, today)

        rows.append({
            "company_name": name,
            "agencies": sorted({x.get("agency", "") for x in acts if x.get("agency")}),
            "latest_action": latest.get("action", ""),
            "latest_rating": latest.get("rating", ""),
            "latest_date": latest.get("date", ""),
            "source_url": latest.get("source_url", ""),
            "rated_amount_cr": amounts.get(name),
            **pred,
        })

    # Unknown due dates sort last: they are not urgent, they are unmeasured.
    rows.sort(key=lambda r: (r["days_until"] is None, r["days_until"]))
    return rows


# ── optional PDF enrichment ──────────────────────────────────────────────────

def extract_from_rationale(pdf_bytes: bytes) -> dict:
    """Previous-action date and rated amount out of one rationale PDF.

    pdfplumber is imported here, not at module scope, and is not a runtime
    requirement: the calendar works without it on action dates alone, and a
    missing parser must degrade the answer rather than fail the request.
    """
    try:
        import pdfplumber
    except ImportError:
        log.info("renewal: pdfplumber not installed, skipping PDF enrichment")
        return {}

    import io
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            # The facilities table and the totals are on the first pages; the
            # rest is narrative and boilerplate. Reading 3 pages instead of 9
            # is roughly a third of the parse cost per document.
            text = "\n".join((p.extract_text() or "") for p in pdf.pages[:3])
    except Exception as e:
        # Expected scraper rot, not a bug: an agency serving an HTML error page
        # with a .pdf URL lands here. The message names the cause; a traceback
        # on every rotten document would only train people to ignore the log.
        log.warning("renewal: could not parse rationale PDF (%s)", e)
        return {}

    return {
        "previous_action_date": parse_previous_action_date(text),
        "rated_amount_cr": parse_rated_amount(text),
    }


async def enrich_from_rationales(rows: list[dict], limit: int = 10) -> int:
    """Fetch the top `limit` issuers' rationale PDFs and sharpen their rows.

    Only Brickwork publishes a fetchable rationale document: Acuité's press
    releases are HTML pages without the facilities table, and India Ratings
    renders client-side. So this improves the rows it can and silently leaves
    the rest on the annual default - which is the honest outcome, and why
    `basis` exists on every row.

    Bounded concurrency and a hard cap, deliberately: this is the only part of
    the calendar that makes network calls, and an unbounded fan-out across a
    few hundred archived issuers would hammer one agency's CDN.
    """
    from backend.pipeline import cra_press

    targets = [r for r in rows if (r.get("source_url") or "").lower().endswith(".pdf")][:limit]
    if not targets:
        return 0

    sem = asyncio.Semaphore(4)
    client = cra_press._get_client()

    async def one(row: dict) -> bool:
        async with sem:
            try:
                resp = await client.get(row["source_url"], timeout=45)
                if resp.status_code != 200 or not resp.content.startswith(b"%PDF-"):
                    return False
                got = extract_from_rationale(resp.content)
            except Exception as e:
                log.info("renewal: rationale fetch failed for %s (%s)",
                         row.get("company_name"), e)
                return False

        changed = False
        if got.get("rated_amount_cr") is not None:
            row["rated_amount_cr"] = got["rated_amount_cr"]
            changed = True
        prev = got.get("previous_action_date")
        if prev:
            # Re-predict: a measured interval supersedes the annual floor.
            row.update(predict_next_review(row["latest_date"], prev))
            changed = True
        return changed

    results = await asyncio.gather(*(one(r) for r in targets), return_exceptions=True)
    enriched = sum(1 for r in results if r is True)

    # Enrichment can move a due date, so the calendar's order is only valid
    # after it has run.
    rows.sort(key=lambda r: (r["days_until"] is None, r["days_until"]))
    return enriched


# -- self-check (no network) -------------------------------------------------

def _demo() -> None:
    today = datetime(2026, 9, 11)

    # -- the header format seen live on Brickwork rationales
    assert parse_previous_action_date("Previous (19-August-2025) Present") == "19-08-2025"
    assert parse_previous_action_date("Previous(1 Aug 2025)") == "01-08-2025"
    assert parse_previous_action_date("Previous (31-February-2025)") == "", \
        "an impossible date must be refused, not coerced"
    assert parse_previous_action_date("no such header") == ""
    assert parse_previous_action_date("Previous (19-Smarch-2025)") == ""

    # -- amounts come from the summary region, first figure, not the largest
    txt = ("Bank Loan Facilities of Rs. 125.00 crores ... Sub-Total Rs. 14.5 Crs "
           "... Grand total Rs. 1,250.75 Crores only")
    assert parse_rated_amount(txt) == 125.0, parse_rated_amount(txt)
    assert parse_rated_amount("Rs. 50 Cr") == 50.0
    assert parse_rated_amount("no money here") is None
    assert parse_rated_amount("Rs. 1,250.75 Crores") == 1250.75, "thousands separators"

    # The regression that made this rule necessary: a sensitivity threshold
    # quoted below the rated book must never be read as the issuer's size.
    live = ("ratings for the Bank Loan Facilities of Rs. 125.00 crores "
            "RATING SENSITIVITIES Positive: revenue above Rs. 500 Crs")
    assert parse_rated_amount(live) == 125.0, parse_rated_amount(live)

    # -- a measured interval beats the annual default and says which it used
    obs = predict_next_review("10-09-2026", "19-08-2025", today)
    assert obs["basis"] == "observed", obs
    assert obs["interval_days"] == 387, obs
    assert obs["due_date"] == "02-10-2027", obs

    ann = predict_next_review("10-09-2026", "", today)
    assert ann["basis"] == "annual" and ann["interval_days"] == 365, ann
    assert ann["due_date"] == "10-09-2027", ann

    # -- a nonsense gap must not project years out and vanish from the calendar
    wild = predict_next_review("10-09-2026", "01-01-2015", today)
    assert wild["interval_days"] == 365 and "out of band" in wild["basis"], wild

    # -- an unusable date is reported as unknown, never silently dropped
    bad = predict_next_review("", "", today)
    assert bad["urgency"] == "unknown" and bad["due_date"] == "", bad

    # -- urgency bands
    assert predict_next_review("15-09-2025", "", today)["urgency"] == "due", \
        "a rating 365 days old is due now"
    assert predict_next_review("01-01-2026", "", today)["urgency"] == "later"
    assert predict_next_review("01-01-2024", "", today)["urgency"] == "stale"

    # -- the calendar groups by issuer, so nine facilities are one call
    actions = [
        {"company_name": "Gulzar Motors Pvt. Ltd.", "agency": "BRICKWORK",
         "rating": "BWR BBB-", "action": "Upgraded", "date": "10-09-2026"},
        {"company_name": "Gulzar Motors Pvt. Ltd.", "agency": "BRICKWORK",
         "rating": "BWR A3", "action": "Assigned", "date": "10-09-2026"},
        {"company_name": "Gulzar Motors Pvt. Ltd.", "agency": "BRICKWORK",
         "rating": "BWR B-", "action": "Downgraded", "date": "19-08-2025"},
        {"company_name": "Older Co", "agency": "INDRA",
         "rating": "IND A", "action": "Affirmed", "date": "01-10-2025"},
        {"company_name": "", "agency": "INDRA", "date": "01-10-2025"},
    ]
    cal = build_calendar(actions, amounts={"Gulzar Motors Pvt. Ltd.": 125.0}, today=today)
    assert len(cal) == 2, cal
    g = [r for r in cal if r["company_name"].startswith("Gulzar")][0]
    assert g["rated_amount_cr"] == 125.0
    assert g["latest_date"] == "10-09-2026"
    # The earlier archived action supplied the interval - no PDF needed.
    assert g["basis"] == "observed" and g["interval_days"] == 387, g
    assert g["agencies"] == ["BRICKWORK"]

    # Soonest first: Older Co is due 01-10-2026, Gulzar not until 2027.
    assert cal[0]["company_name"] == "Older Co", [r["company_name"] for r in cal]

    # A PDF-supplied previous date overrides the archive's.
    cal2 = build_calendar(actions, previous_dates={"Gulzar Motors Pvt. Ltd.": "10-09-2025"},
                          today=today)
    g2 = [r for r in cal2 if r["company_name"].startswith("Gulzar")][0]
    assert g2["interval_days"] == 365 and g2["basis"] == "observed", g2

    # An unparseable PDF degrades to {} rather than raising.
    assert extract_from_rationale(b"not a pdf") == {}

    print("renewal self-check: ok")


if __name__ == "__main__":
    _demo()
