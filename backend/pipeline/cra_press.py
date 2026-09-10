"""
CRA PRESS — recent rating actions scraped directly off the 7 agencies' own
sites, not from an exchange.

NSE/BSE disclosures (nse_ratings.py, bse_scraper.py) only ever cover listed
issuers. The bulk of India's rated universe is unlisted bank-loan borrowers —
a private company with a CC/OD facility never files an NSE/BSE disclosure,
so the only place its rating ever surfaces is the CRA's own press-release
page. This module is that second leg.

Reality check after actually probing all 7 live sites (Sep 2026), no
headless browser, plain httpx only:

  ACUITE      WORKS   connect.acuite.in/liveratings is a plain server-
                      rendered HTML table — date, company, per-instrument
                      rating and action, no JS needed.
  BRICKWORK   WORKS   the public homepage carries a "Rating Rationales"
                      feed with per-instrument rating + company + status
                      baked into each link's text/href. Its dedicated
                      PressRelease.aspx search page is a classic ASP.NET
                      WebForms postback grid behind __VIEWSTATE and ships
                      no data in the initial HTML — not usable here, but
                      the homepage feed covers the same ground.
  INDRA       WORKS   /home/GetRatingNews is a plain JSON route, no auth, no
                      cookies - a rolling "latest ~10 actions" feed. Rating and
                      action ride inside pressReleaseTitle text, so both are
                      regexed out rather than read from a field.
  CARE        WORKS    but LOOKUP-ONLY: careratings.com/header/searchlist
                      resolves a name to an opaque CompanyID, and
                      /getSearchprintrating returns that company's instruments
                      and ratings. There is no recent-actions feed, so CARE can
                      answer "what is X rated" but never "who moved this week" -
                      it therefore appears in fetch_for_company, not in the queue.
  CRISIL      BLOCKED credit_history.py's search_url 404s (site moved to
                      crisilratings.com); the real rating search sits behind a
                      reCAPTCHA, so it is off-limits by policy, not just by
                      engineering.
  ICRA        BLOCKED the one reachable endpoint returns company names with no
                      rating; the rating lookup itself needs a live CSRF session.
  INFOMERICS  BLOCKED Next.js app; recent-ratings streams via an RSC
                      payload, not a fetchable JSON route.

BLOCKED means genuinely blocked, not "didn't try" — see `sources` in the
returned dict. A source that can't be reached is never reported as "no
ratings found"; that distinction is the whole point of data_status.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from backend.pipeline.bse_scraper import _norm
from backend.pipeline.nse_ratings import _MONTHS, _date_key

ACUITE_URL = "https://connect.acuite.in/liveratings?page=1"
BRICKWORK_URL = "https://www.brickworkratings.com/"
INDRA_URL = "https://www.indiaratings.co.in/home/GetRatingNews"
CARE_SEARCH_URL = "https://www.careratings.com/header/searchlist"
CARE_RATING_URL = "https://www.careratings.com/getSearchprintrating"

# No working scrape path at all (see module docstring).
_STATICALLY_BLOCKED = ["CRISIL", "ICRA", "INFOMERICS"]

# Reachable per company, but with no recent-actions feed — so they can answer
# "what is X rated" and never "who moved this week". Reported as "lookup_only"
# rather than "blocked", because calling them blocked would understate coverage
# and calling them ok would overstate it.
_LOOKUP_ONLY = ["CARE"]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True)
    return _client


# -- Circuit breaker per source, same shape as nse_ratings/bse_scraper -------
_breaker = {name: {"fails": 0, "until": 0.0}
            for name in ("ACUITE", "BRICKWORK", "INDRA", "CARE")}


def _tripped(source: str) -> bool:
    return time.time() < _breaker[source]["until"]


def _record(source: str, ok: bool) -> None:
    b = _breaker[source]
    if ok:
        b["fails"] = 0
    else:
        b["fails"] += 1
        if b["fails"] >= 4:
            b["until"] = time.time() + 600
            b["fails"] = 0


# -- TTL cache: these are "latest page" snapshots, cheap to reuse for a bit --
_TTL = 900  # 15 min
_cache: dict[str, tuple[float, list[dict]]] = {}


# ── ACUITE ───────────────────────────────────────────────────────────────────
# ponytail: the "action" text (Reaffirmed/Assigned/...) rides in an <img
# title='...'> that Acuité's own template double-escapes into visible text
# rather than a real tag (verified live, Sep 2026) - so we regex the decoded
# text instead of walking a DOM node. If they ever fix the template this still
# works, since <img title=...> would then appear as a real tag whose
# attributes get_text() drops - re-check against a live fetch if this file's
# self-check ever starts failing on real data.

_ACUITE_DATE_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3})\s+(\d{2,4})", re.I)
_TITLE_RE = re.compile(r"title=['\"]([^'\"]+)['\"]", re.I)
_FAKE_IMG_RE = re.compile(r"<img.*?>", re.I)


def _acuite_date(raw: str) -> str:
    m = _ACUITE_DATE_RE.search(raw or "")
    if not m:
        return ""
    day, mon, yr = m.groups()
    yr = f"20{yr}" if len(yr) == 2 else yr
    return f"{int(day):02d}-{_MONTHS.get(mon.title(), '01')}-{yr}"


def parse_acuite_html(html: str) -> list[dict]:
    """Pure parse: Acuité's live-ratings table -> action dicts. One dict per
    instrument row (a company can carry several facilities, each with its own
    rating and action)."""
    soup = BeautifulSoup(html, "lxml")
    actions: list[dict] = []
    cur: dict | None = None
    for tr in soup.find_all("tr"):
        classes = tr.get("class") or []
        if "accordion-toggle" in classes:
            date_cell = tr.find(class_="td-date")
            company_cell = tr.find(class_="td-company")
            a = company_cell.find("a") if company_cell else None
            cur = {
                "date": _acuite_date(date_cell.get_text(strip=True) if date_cell else ""),
                "company_name": (a.get_text(strip=True) if a
                                  else (company_cell.get_text(strip=True) if company_cell else "")),
                "source_url": a.get("href", "") if a else "",
            }
        elif cur and any(c.startswith("sub") for c in classes):
            rating_cell = tr.find(class_="live_actions")
            if not rating_cell:
                continue
            raw = re.sub(r"\s+", " ", rating_cell.get_text(" ", strip=True)).strip()
            if not raw:
                continue
            title_m = _TITLE_RE.search(raw)
            action = title_m.group(1).strip() if title_m else "Rating action"
            rating = re.sub(r"\s+", " ", _FAKE_IMG_RE.sub("", raw)).strip()
            actions.append({
                "agency": "ACUITE",
                "rating": rating,
                "action": action,
                "date": cur["date"],
                "isin": "",
                "company_name": cur["company_name"],
                "source_url": cur["source_url"],
            })
    return actions


# ── BRICKWORK ────────────────────────────────────────────────────────────────
# The homepage's "Rating Rationales" feed encodes rating, company and
# instrument in one anchor's aria-label/text, and status in the href: BWR
# hosts three rationale flavours (Initial / INC / NOCWD-withdrawn) on
# bcrisp.in, plus plain PDFs under Admin/PressRelease/ for routine actions.

_BRICKWORK_LABEL_RE = re.compile(
    r"^(BWR\s*[^:]+?)\s*:\s*(.+?)\s+(Short Term|Long Term|Issuer Rating)\b", re.I)
_BRICKWORK_DATE_RE = re.compile(r"(\d{1,2})([A-Za-z]{3})(\d{4})")


def parse_brickwork_html(html: str) -> list[dict]:
    """Pure parse: Brickwork homepage press-release feed -> action dicts."""
    soup = BeautifulSoup(html, "lxml")
    ul = soup.find("ul", class_="press-release-list")
    if not ul:
        return []
    actions: list[dict] = []
    for li in ul.find_all("li"):
        a = li.find("a")
        if not a:
            continue
        label = (a.get("aria-label") or a.get_text(strip=True) or "").strip()
        href = a.get("href") or ""
        m = _BRICKWORK_LABEL_RE.match(label)
        if not m:
            continue
        rating, company, instrument = m.group(1).strip(), m.group(2).strip(), m.group(3)

        low_label, low_href = label.lower(), href.lower()
        if "withdraw" in low_label or "nocwd" in low_href:
            action = "Withdrawn"
        elif "incnew" in low_href:
            action = "Issuer Not Cooperating"
        elif "initial" in low_href:
            action = "Assigned"
        else:
            action = "Rating action (see filing)"

        dm = _BRICKWORK_DATE_RE.search(href)
        date = (f"{int(dm.group(1)):02d}-{_MONTHS.get(dm.group(2).title(), '01')}-{dm.group(3)}"
                if dm else "")

        actions.append({
            "agency": "BRICKWORK",
            "rating": rating,
            "action": action,
            "date": date,
            "isin": "",
            "company_name": company,
            "source_url": href if href.startswith("http") else f"https://www.brickworkratings.com/{href}",
            "instrument": instrument,
        })
    return actions


# ── INDIA RATINGS (Ind-Ra) ───────────────────────────────────────────────────
# /home/GetRatingNews is plain JSON, no auth. It is a rolling "latest ~10
# actions" feed, so it keeps the queue fresh but will never backfill history.
# Neither the rating nor the action has its own field: both live inside
# pressReleaseTitle prose, e.g.
#   " India Ratings Affirms GRP Circular Solutions's Bank Loan Facilities at
#     'IND BB+'/Stable"
# so both are regexed out of the title. A title we cannot parse yields a row
# with an empty rating rather than a guessed one.

_INDRA_RATING_RE = re.compile(
    r"\bIND\s?(AAA|AA[+-]?|A1[+]?|A2[+]?|A3[+]?|A4[+]?|BBB[+-]?|BB[+-]?|"
    r"B[+-]?|A[+-]?|C|D)",
    re.I)
_INDRA_ACTION_RE = re.compile(
    r"\b(Affirms?|Upgrades?|Downgrades?|Assigns?|Withdraws?|Rates?|Places?|"
    r"Migrates?|Revises?|Reaffirms?)\b", re.I)
_INDRA_DATE_RE = re.compile(r"([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{4})")

# Ind-Ra migrates an issuer to the non-cooperating scale rather than tagging it,
# so INC has to be read from the title wording.
_INDRA_INC_RE = re.compile(r"non[- ]cooperat|issuer\s+did\s+not\s+cooperat", re.I)


def _indra_date(raw: str) -> str:
    """'Sep 10, 2026' -> '10-09-2026' (the DD-MM-YYYY the rest of the app uses)."""
    m = _INDRA_DATE_RE.search(raw or "")
    if not m:
        return ""
    mon, day, yr = m.groups()
    return f"{int(day):02d}-{_MONTHS.get(mon.title(), '01')}-{yr}"


def parse_indra_json(payload) -> list[dict]:
    """Pure parse: Ind-Ra's GetRatingNews array -> action dicts."""
    if not isinstance(payload, list):
        return []
    actions = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        company = (item.get("issuerName") or "").strip()
        if not company:
            continue
        title = (item.get("pressReleaseTitle") or "").strip()

        rm = _INDRA_RATING_RE.search(title)
        rating = f"IND {rm.group(1).upper()}" if rm else ""

        if _INDRA_INC_RE.search(title):
            action = "Issuer Not Cooperating"
        else:
            am = _INDRA_ACTION_RE.search(title)
            action = am.group(1).title() if am else "Rating action (see press release)"

        pr_id = item.get("pressReleaseID")
        actions.append({
            "agency": "INDRA",
            "rating": rating,
            "action": action,
            "date": _indra_date(item.get("prDate", "")),
            "isin": "",
            "company_name": company,
            "source_url": (f"https://www.indiaratings.co.in/pressrelease/{pr_id}"
                           if pr_id else "https://www.indiaratings.co.in/"),
        })
    return actions


# ── CARE RATINGS ─────────────────────────────────────────────────────────────
# Lookup-only: a name resolves to an opaque encrypted CompanyID, which then
# returns that company's instruments and current ratings. There is no
# recent-actions feed, so CARE can answer "what is X rated" but never "who
# moved this week" — it belongs in fetch_for_company, not in the queue.
#
# The payload carries no action verb and no date. We deliberately emit action
# "Current rating" and an empty date rather than inventing either: a fabricated
# date would put a stale rating inside a "last 30 days" window.

def parse_care_ratings(payload, company_fallback: str = "") -> list[dict]:
    """Pure parse: CARE's getSearchprintrating JSON -> action dicts."""
    rows = (payload or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    actions = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        company = (row.get("Company") or company_fallback or "").strip()
        for inst in row.get("CompanyInstrument") or []:
            rating = (inst.get("Rating") or "").strip()
            if not rating or not company:
                continue
            # CARE writes a withdrawal into the rating cell itself.
            action = "Withdrawn" if "withdraw" in rating.lower() else "Current rating"
            actions.append({
                "agency": "CARE",
                "rating": rating,
                "action": action,
                "date": "",
                "isin": "",
                "company_name": company,
                "source_url": "https://www.careratings.com/",
                "instrument": (inst.get("Instrument") or "").strip(),
            })
    return actions


# ── network fetchers ────────────────────────────────────────────────────────

async def _fetch_source(source: str, url: str, parse) -> tuple[list[dict], str]:
    """Shared fetch/cache/breaker plumbing for one HTML source.
    status: "ok" (data found), "none" (page answered, nothing parsed),
    "blocked" (breaker tripped / non-200 / network error — NOT verified empty)."""
    cached = _cache.get(source)
    if cached and time.time() - cached[0] < _TTL:
        return cached[1], ("ok" if cached[1] else "none")
    if _tripped(source):
        return [], "blocked"
    try:
        r = await _get_client().get(url)
        if r.status_code != 200:
            _record(source, False)
            return [], "blocked"
        _record(source, True)
        actions = parse(r.text)
    except Exception:
        _record(source, False)
        return [], "blocked"
    _cache[source] = (time.time(), actions)
    return actions, ("ok" if actions else "none")


def _within_days(date_str: str, days: int) -> bool:
    """Unknown date -> keep it visible rather than silently drop it."""
    if not date_str:
        return True
    try:
        d = datetime.strptime(date_str, "%d-%m-%Y")
    except ValueError:
        return True
    return d >= datetime.now() - timedelta(days=days)


async def _fetch_json_source(source: str, url: str, parse) -> tuple[list[dict], str]:
    """Same contract as _fetch_source, for endpoints that answer JSON.

    Decoding is forced to UTF-8: Ind-Ra serves curly quotes without declaring a
    charset, and httpx's fallback mangles them into replacement characters that
    then leak into company names."""
    cached = _cache.get(source)
    if cached and time.time() - cached[0] < _TTL:
        return cached[1], ("ok" if cached[1] else "none")
    if _tripped(source):
        return [], "blocked"
    try:
        r = await _get_client().get(url)
        if r.status_code != 200:
            _record(source, False)
            return [], "blocked"
        _record(source, True)
        actions = parse(json.loads(r.content.decode("utf-8", errors="replace")))
    except Exception:
        _record(source, False)
        return [], "blocked"
    _cache[source] = (time.time(), actions)
    return actions, ("ok" if actions else "none")


async def fetch_recent_actions(days: int = 30) -> dict:
    """
    Recent rating actions across the 7 CRAs, best-effort.

    Three agencies carry a recent-actions feed (ACUITE, BRICKWORK, INDRA). CARE
    is reachable but lookup-only, so it reports "lookup_only" here and is used by
    fetch_for_company instead. The rest report "blocked". A caller must never be
    able to mistake "we didn't try" for "nothing to report".
    """
    acuite_actions, acuite_status = await _fetch_source("ACUITE", ACUITE_URL, parse_acuite_html)
    brickwork_actions, brickwork_status = await _fetch_source("BRICKWORK", BRICKWORK_URL, parse_brickwork_html)
    indra_actions, indra_status = await _fetch_json_source("INDRA", INDRA_URL, parse_indra_json)

    combined = acuite_actions + brickwork_actions + indra_actions
    filtered = [a for a in combined if _within_days(a["date"], days)]
    filtered.sort(key=lambda a: _date_key(a["date"]), reverse=True)

    sources = {k: "blocked" for k in _STATICALLY_BLOCKED}
    sources.update({k: "lookup_only" for k in _LOOKUP_ONLY})
    sources["ACUITE"] = acuite_status
    sources["BRICKWORK"] = brickwork_status
    sources["INDRA"] = indra_status

    attempted = [acuite_status, brickwork_status, indra_status]
    if filtered:
        data_status = "ok"
    elif all(s == "none" for s in attempted):
        data_status = "none_found"
    else:
        data_status = "unverified"

    return {"actions": filtered, "sources": sources, "data_status": data_status}


def _care_match(query: str, candidate: str) -> bool:
    """Is CARE's search hit actually the company we asked about?

    CARE's autocomplete is a substring search, so "Arka Eduserve" happily returns
    a different Arka. Attaching another company's rating — and worse, another
    company's default — to a lead a salesperson is about to call is the single
    most damaging error this module can make, so the bar is exact-after-folding
    rather than fuzzy. A missed match costs one absent rating; a wrong match
    costs the tool its credibility."""
    q, c = _norm(query), _norm(candidate)
    return bool(q) and bool(c) and (q == c or q.startswith(c) or c.startswith(q))


async def fetch_care_for_company(company_name: str) -> list[dict]:
    """CARE's two-step lookup: name -> opaque CompanyID -> that company's
    instruments and current ratings.

    Only search hits that pass `_care_match` are followed, and only the first of
    those, so one lead never costs more than two calls."""
    if _tripped("CARE"):
        return []
    try:
        client = _get_client()
        found = await client.get(CARE_SEARCH_URL, params={"cinput": company_name})
        if found.status_code != 200:
            _record("CARE", False)
            return []
        hits = (found.json() or {}).get("data") or []
        top = next((h for h in hits
                    if _care_match(company_name, h.get("CompanyName", ""))), None)
        if top is None:
            _record("CARE", True)     # answered, just no confident match
            return []
        detail = await client.get(CARE_RATING_URL,
                                  params={"companyName": top.get("CompanyID", "")})
        if detail.status_code != 200:
            _record("CARE", False)
            return []
        _record("CARE", True)
        return parse_care_ratings(detail.json(), top.get("CompanyName", company_name))
    except Exception:
        _record("CARE", False)
        return []


async def fetch_for_company(company_name: str) -> list[dict]:
    """Everything the CRA sites say about one company.

    Two different shapes merged: the feed agencies give dated *actions* (cast a
    wide 365-day net and filter locally, since these are latest-page snapshots
    rather than an indexed search), while CARE gives undated *current ratings*
    from a real per-company lookup."""
    target = _norm(company_name)
    if not target:
        return []
    data = await fetch_recent_actions(days=365)
    from_feeds = [a for a in data["actions"]
                  if target in _norm(a["company_name"]) or _norm(a["company_name"]) in target]
    return from_feeds + await fetch_care_for_company(company_name)


# ── self-check (no network) ─────────────────────────────────────────────────

_ACUITE_FIXTURE = """
<table><tbody>
<tr class="accordion-toggle" id="42291">
  <th class="td-date">9th Sep 26</th>
  <td class="td-company"><a href="https://connect.acuite.in/fcompany-details/SPECTRON_ENGINEERS_PRIVATE_LIMITED/9th_Sep_26">SPECTRON ENGINEERS PRIVATE LIMITED</a></td>
</tr>
<tr class="sub44031">
  <td></td><td>Cash Credit</td><td class="live_bank"></td><td class="live_instrument">Long-term</td>
  <td class="live_quantum">INR 22 Cr</td>
  <td class="live_actions"><span>ACUITE <span> BB+ </span><span> </span><span>Stable</span><span></span>
    <span class="sym-padding"> &lt;img src='images/reaffirmed.png' class='symbol-img' title='Reaffirmed'&gt; </span></span></td>
</tr>
<tr class="sub44031">
  <td></td><td>Bank Guarantee</td><td class="live_bank"></td><td class="live_instrument">Short-term</td>
  <td class="live_quantum">INR 19 Cr</td>
  <td class="live_actions"><span>ACUITE <span> </span><span> A4+ </span><span></span>
    <span class="sym-padding"> &lt;img src='images/assigned.png' class='symbol-img' title='Assigned'&gt; </span></span></td>
</tr>
</tbody></table>
"""

_BRICKWORK_FIXTURE = """
<div class="press-release"><ul class="press-release-list">
<li><a aria-label="BWR A4 + : Shrijee Lifestyle Pvt. Ltd. Short Term Bank Loan Rs64.00 Crore PDF File Opens in a new window"
       href="https://bcrisp.in///BLRHTML/HTMLDocument/ViewRatingRationaleINCNew?id=219010">BWR A4 + : Shrijee Lifestyle Pvt. Ltd. Short Term Bank Loan</a></li>
<li><a aria-label="BWR A3 : Hutni Projekt FM (India) Pvt. Ltd. Short Term Bank Loan Rs70.00 Crore PDF File Opens in a new window"
       href="https://bcrisp.in///BLRHTML/HTMLDocument/ViewRatingRationaleInitial?id=218904">BWR A3 : Hutni Projekt FM (India) Pvt. Ltd. Short Term Bank Loan</a></li>
<li><a aria-label="BWR BB : Atithi Paper LLP Long Term Bank Loan Rs3.87 Crore (Rs.1.83 Cr proposed loan Withdrawn) PDF File Opens in a new window"
       href="Admin/PressRelease/X-Atithi-Paper-LLP-7Sep2026.docx.pdf">BWR BB : Atithi Paper LLP Long Term Bank Loan</a></li>
<li><a aria-label="BWR A4 : Maheshwari Agro Short Term Bank Loan PDF File Opens in a new window"
       href="http://bcrisp.in///BLRHTML/HTMLDocument/ViewRatingRationaleNOCWD?id=218277">BWR A4 : Maheshwari Agro Short Term Bank Loan</a></li>
</ul></div>
"""


def _demo() -> None:
    acuite = parse_acuite_html(_ACUITE_FIXTURE)
    assert len(acuite) == 2, acuite
    assert acuite[0]["agency"] == "ACUITE"
    assert acuite[0]["company_name"] == "SPECTRON ENGINEERS PRIVATE LIMITED", acuite[0]
    assert acuite[0]["date"] == "09-09-2026", acuite[0]
    assert acuite[0]["rating"] == "ACUITE BB+ Stable", acuite[0]
    assert acuite[0]["action"] == "Reaffirmed", acuite[0]
    assert acuite[0]["isin"] == ""
    assert acuite[1]["action"] == "Assigned" and "A4+" in acuite[1]["rating"], acuite[1]
    for a in acuite:
        assert {"agency", "rating", "action", "date", "isin", "company_name", "source_url"} <= a.keys()

    bwr = parse_brickwork_html(_BRICKWORK_FIXTURE)
    assert len(bwr) == 4, bwr
    inc = next(x for x in bwr if x["company_name"].startswith("Shrijee"))
    assert inc["action"] == "Issuer Not Cooperating", inc
    assert inc["rating"] == "BWR A4 +", inc

    initial = next(x for x in bwr if "Hutni" in x["company_name"])
    assert initial["action"] == "Assigned", initial

    withdrawn_wording = next(x for x in bwr if "Atithi" in x["company_name"])
    assert withdrawn_wording["action"] == "Withdrawn", withdrawn_wording
    assert withdrawn_wording["date"] == "07-09-2026", withdrawn_wording

    nocwd = next(x for x in bwr if "Maheshwari" in x["company_name"])
    assert nocwd["action"] == "Withdrawn", nocwd
    assert nocwd["date"] == "", nocwd  # bcrisp.in links carry no filename date - honestly blank

    for a in bwr:
        assert {"agency", "rating", "action", "date", "isin", "company_name", "source_url"} <= a.keys()
        assert a["source_url"].startswith("http")

    # date-window filter: unknown dates stay visible, old known dates drop out
    assert _within_days("", 30) is True
    assert _within_days("07-09-2026", 30) is True
    assert _within_days("01-01-2020", 30) is False

    print("cra_press self-check: ok")


if __name__ == "__main__":
    _demo()
