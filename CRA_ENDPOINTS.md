# CRA SPA endpoint investigation (Sep 2026)

Scope: the 5 agencies `cra_press.py` currently lists as `_STATICALLY_BLOCKED`
(CRISIL, ICRA, CARE, India Ratings, Infomerics). Each site's rating-search /
press-release page was opened in a real browser (Claude Browser pane), the
network tab inspected for the XHR/fetch call that actually populates the
page, and every candidate endpoint was replayed **outside the browser** with
a throwaway plain-`httpx` script (no cookies, no session) as the decisive
test. Scripts used: `test_care3.py`, `test_indra.py`, `test_infomerics3.py`
in the scratchpad dir (not committed).

## Result: 2 of 5 have a replayable JSON endpoint (CARE, India Ratings). One
more (Infomerics) has replayable data but it's embedded in an HTML payload,
not a clean JSON route, and lacks the rating grade. CRISIL and ICRA are
genuinely blocked.

---

## CARE Ratings - WORKS (plain httpx, no cookies)

Two-step flow, both plain GET, both server-rendered JSON (Laravel backend):

**1. Company search (autocomplete)**
```
GET https://www.careratings.com/header/searchlist?cinput=<company text>
```
No auth, no headers required beyond a normal `User-Agent`. Sample response:
```json
{"data":[{"CompanyID":"rnpuTCoMVDQtdTYuJtKLuw==","CompanyName":"Adani Agri Fresh Limited","CommonContent":[]}, ...]}
```
`CompanyID` is an encrypted (AES, base64-ish) opaque token - treat as a
capability string, not something to decode.

**2. Rating detail (per company)**
```
GET https://www.careratings.com/getSearchprintrating?companyName=<CompanyID>
```
Sample response:
```json
{"data":[{"CompanyID":9569,"Company":"Adani Agri Fresh Limited",
  "CompanyInstrument":[
    {"Instrument":"CC/Packing Credit","RatingAmount":"1450.00","Rating":"CARE A-; Stable / CARE A2+"},
    {"Instrument":"Long Term","RatingAmount":"0.00","Rating":"Withdrawn"},
    {"Instrument":"Working Capital Limits","RatingAmount":"100.00","Rating":"CARE A2+"}
  ]}]}
```
Sibling endpoints on the same `companyName=<CompanyID>` param, also plain
JSON, also replayed successfully: `getSearchBankListed` (per-facility bank
detail), `getSearchBreadcrumb` (company metadata incl. numeric internal
`CompanyID`), `getSearchprdocument?...&YearID=2026` (press-release PDF list
with `PublishedDate`, e.g. `"2026-04-07 00:00:00.000"`).

**Field mapping:**
| our contract | source field |
|---|---|
| agency | constant `"CARE"` |
| company_name | `data[].Company` |
| rating | `data[].CompanyInstrument[].Rating` (rating + outlook combined, e.g. `"CARE A-; Stable / CARE A2+"`) |
| action | not in this payload - would need `getSearchprdocument`'s PDF, opened separately |
| date | `getSearchprdocument[].PRDocument[].PublishedDate` |
| isin | not present in any of these payloads |

**httpx replay test:** `python test_care3.py` / `test_care2.py` →
`searchlist` and `getSearchprintrating` both returned **HTTP 200** with the
same live data as the browser (verified against "Adani Agri Fresh Limited",
matched byte-for-byte with what the rendered page showed). No cookies, no
CSRF token, no referrer needed for the two data calls tested. **This is a
genuine win - works cold, outside the browser.**

Caveat: `/search?Id=<CompanyID>` (the human-facing detail page CARE's own
JS loads data into) returns a generic shell when fetched directly - it does
NOT contain the rating table. The actual data lives only in the small JSON
endpoints above, which must be called directly.

---

## India Ratings (Ind-Ra) - WORKS (plain httpx, no cookies)

The homepage's "recent rating actions" list is populated by:
```
GET https://www.indiaratings.co.in/home/GetRatingNews
```
No params, no auth. Sample response (array, most recent first):
```json
[{"issuerName":"GRP CIRCULAR SOLUTIONS LIMITED","pressReleaseID":85181,
  "pressReleaseTitle":" India Ratings Affirms GRP Circular Solutions's Bank Loan Facilities at 'IND BB+'/Stable",
  "prDate":"Sep 10, 2026","industryName":"Other Industrial Products",
  "urlKey":"ebyqccr5ahscscx78an8z211", ...}, ...]
```
**Field mapping:**
| our contract | source field |
|---|---|
| agency | constant `"INDRA"` |
| company_name | `issuerName` |
| rating | embedded in `pressReleaseTitle` text (e.g. `'IND BB+'/Stable`) - needs a regex pull, not a clean field |
| action | first verb of `pressReleaseTitle` (Affirms / Upgrades / Downgrades / Rates) |
| date | `prDate` (human string, e.g. `"Sep 10, 2026"`) |
| isin | not present |

**httpx replay test:** `python test_indra.py` → **HTTP 200**, live data,
identical to the browser (same `pressReleaseID`s, same date). Only mild
gotcha: the response has UTF-8 curly-quote characters that need `encoding
utf-8` handling (httpx got them slightly mangled with default settings -
easy fix, not a blocker). **Genuine win - works cold, outside the browser.**

Note: this endpoint is a rolling "latest ~10 actions" feed, not a
company-name search - good for cra_press.py's "recent actions across all
issuers" use case, not for looking up one company. A per-company variant
may exist behind India Ratings' search box but wasn't found in the time
budget for this pass - worth another look if company-level lookups on
India Ratings specifically become a priority.

---

## Infomerics - PARTIAL (data replayable, but not via a real API, and no rating field)

No XHR/fetch call powers the homepage's "Recent Ratings" carousel - this is
a Next.js App Router site, and the data actually rides inside the
server-rendered HTML itself, as an escaped JSON blob inside a
`self.__next_f.push([...])` RSC hydration chunk. Confirmed by fetching
`https://www.infomerics.com/` with plain httpx (no browser) and finding the
same company names/dates verbatim in the raw response text - e.g.
`"CompanyName":"Yash Resources Recycling Limited (Erstwhile Yash Pigments Limited)","Date":"2026-09-10"`.

This is real, and it does survive outside the browser (`test_infomerics3.py`
confirms `httpx.get("https://www.infomerics.com/")` returns the same
company/date pairs as the rendered page) - but:
- It is not a dedicated endpoint, just the homepage's full HTML (233KB),
  requiring a regex/JSON-in-string extraction rather than a normal
  `response.json()` call.
- It only carries `CompanyName` + `Date` + a link to a PDF press release -
  **no rating grade or action text** anywhere in the payload. The actual
  "IND AA/Stable"-style rating only exists inside the linked PDF.
- The deeper listing page (`/ratings/recent-ratings?title=...`) that would
  presumably have the full history is only 14KB and carries none of this
  data - it must lazy-load client-side via a route this investigation
  didn't locate.

Given no rating field is available at all, this does **not** count as a
working endpoint for our contract (agency/company/rating/action/date/isin)
- it can at most backfill company-name + date + a PDF link to page later.

---

## CRISIL - BLOCKED

`credit_history.py`'s configured URL 404s (site moved from crisil.com to
crisilratings.com). The real "Rating List and Scales" page
(`https://www.crisilratings.com/.../rating-list-and-scales.html`) has a
company-name search box (`input[name="cname"]` inside `#frmNewGlobalSearch`)
- but the form is wrapped in a visible Google reCAPTCHA v2 (`recaptcha/api2`
iframe, confirmed via DOM inspection). Per the task's rules, CAPTCHA-gated
forms are off-limits. No JSON endpoint was reachable without first clearing
that CAPTCHA. **Genuinely blocked**, not merely unattempted.

## ICRA - BLOCKED

`icra.in`'s header search box does hit a real, unauthenticated endpoint -
```
POST https://www.icra.in/Rating/GetRatingCompanys   body: Term=<query>
```
- which returns a plain JSON list of `{id, label}` company matches (this
part alone replays fine outside the browser). But selecting a result does
**not** use that `id` for anything: it re-submits the *typed text* as a
free-text keyword to
```
POST https://www.icra.in/Home/PostGlobalSearchIndex   (CSRF-token gated)
```
which searches press releases/CPR reports by keyword and returned "no
results" for exact legal names in testing (e.g. "Reliance Industries
Limited") - it's a site-wide keyword search, not a rating lookup, and
requires a live CSRF token scraped from the page each time (session-bound).
Guessing at REST-style detail routes (`/Rating/RatingDetails/<id>`,
`/Rating/RatingSummary/<id>`, etc.) all returned the same generic
200-length shell regardless of ID - no working rating-detail route was
found. **Blocked**: the only real data endpoint (`GetRatingCompanys`)
returns company names/IDs only, never an actual rating.

---

## Summary for the headless-browser-infrastructure decision

- **2 of 5** (CARE, India Ratings) have a fully replayable JSON endpoint
  that returns real rating data with zero cookies/session/JS - a plain
  `httpx` script cold-calls them successfully today.
- **1 of 5** (Infomerics) has replayable company+date data but no rating
  field and no clean endpoint (regex-scrape of the homepage HTML).
- **2 of 5** (CRISIL, ICRA) are genuinely blocked: CRISIL by a CAPTCHA on
  the only rating-search form found; ICRA because its only working data
  endpoint returns company names, not ratings, and its real search modal
  needs a live CSRF-token session.
