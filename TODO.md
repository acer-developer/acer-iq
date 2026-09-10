# ACER-IQ - TODO

Live status as of **2026-09-10**, ordered to match [ROADMAP_V3.md](ROADMAP_V3.md).
The rationale for the ordering - and the reframe from "find leads" to "rank on
winnability" - is in that document. The three roadmap framings side by side are
in `roadmap-evolution.html`.

Legend: **FREE** = no new spend · **PAID** = costs money · **DECISION** = needs your call.

---

## Phase 0 - Two audits, no code

Neither needs software, both run on data ACER already holds, and either could be
worth more than the whole build. They also change what the tool should rank.

- [ ] **Empanelment map** - **DECISION** - which banks, PSUs, state industrial
      corporations and MSME schemes list ACER as an approved CRA, and where it is
      absent. If ACER is not on a panel, the bank cannot pick ACER, so no amount
      of issuer targeting helps in that geography.
- [ ] **Churn audit on the existing book** - **DECISION** - which current clients
      are late paying, went quiet at last surveillance, or are shopping a
      competing quote. Retention is cheaper than acquisition and carries no
      adverse-selection risk.

## Phase 1 - Trustworthy foundation

Everything downstream sits on this. Nothing here is glamorous.

- [ ] **Persist every search to Supabase** - **FREE** - `_search_cache` is still
      in-process, so a restart loses it and `/api/export/{id}` 404s. The code path
      exists and logs loudly; it needs `SUPABASE_URL` / `SUPABASE_KEY` set and the
      `searches` table created.
- [ ] **Outcome logging schema** - **FREE** - every lead carries the signal that
      produced it, and its eventual outcome. Must land *before* any lead is
      generated, or the winnability score stays opinion for another six months.
- [ ] **Auth gate + CORS lockdown** - **FREE** - **DECISION**: backend and frontend
      must ship together or the Vercel frontend breaks. Right now anyone with the
      URL can burn our quotas. Supersedes the shared-token idea: use Supabase Auth,
      which Phase 3 needs anyway.
- [ ] **Source-health alarms** - **FREE** - the `sources[]` block already reports
      health per request; nothing *notifies* when a scraper goes dark. Seven CRA
      sites with no APIs will break silently otherwise.
- [ ] **Always-on backend** - **FREE or ~$7/mo** - the free Render dyno sleeps,
      which is the root cause of lost caches and export 404s. Oracle Cloud Always
      Free ARM VM makes this cost nothing; ~$7/mo buys less hassle.
- [ ] **TTL cache for geocode and MCA lookups** - **FREE** - the same company is
      re-fetched on every search.

## Phase 2 - Winnability engine (the product)

All **FREE**, but real scraping work. Full signal spec:
[PIPELINE_RADAR_SPEC.md](PIPELINE_RADAR_SPEC.md).

- [x] **The four winnability flags** - unrated first-timer, issuer-not-cooperating,
      rating withdrawn at issuer request, already rated by two or more agencies.
      Built in `backend/pipeline/winnability.py`, pure function over what
      `fetch_credit_history` already returns, wired into `/api/company-credit` as
      a `winnability` block. Self-check: `python -m backend.pipeline.winnability`.
      **Weights are flat and deliberately un-tuned** - there is no outcome data to
      fit them to yet. Revisit once Phase 1 outcome logging has six months of it.
- [~] **Per-CRA press-release scrapers** - **4 of 7 reachable.** Built in
      `backend/pipeline/cra_press.py`, same action-dict contract as
      `credit_history.py`, per-agency status so no agency can fail silently.
      Self-check: `python -m backend.pipeline.cra_press`.
      - [x] **Acuite** - server-rendered HTML at `connect.acuite.in/liveratings`.
      - [x] **Brickwork** - homepage rating-rationale feed. Its `PressRelease.aspx`
            search page is an ASP.NET `__VIEWSTATE` grid serving no data.
      - [x] **India Ratings** - `/home/GetRatingNews`, plain JSON, no auth. A
            rolling "latest ~10 actions" feed; rating and action are regexed out
            of `pressReleaseTitle` prose, since neither has its own field.
      - [x] **CARE** - reachable but **lookup-only**: a name resolves to an opaque
            CompanyID, which returns that company's instruments and current
            ratings. No recent-actions feed and no dates in the payload, so CARE
            answers "what is X rated" and never "who moved this week". Reported as
            `lookup_only`, and used by the queue's enrichment pass.
      - [ ] **CRISIL** - blocked by reCAPTCHA on its only rating search. Off-limits
            by policy, not just engineering. A headless browser would not change
            this and must not be used to try.
      - [ ] **ICRA** - blocked: the one reachable endpoint returns company names
            with no rating; the rating lookup needs a live CSRF session.
      - [ ] **Infomerics** - partial and not worth wiring: company name and date
            ride inside a Next.js RSC hydration blob on the homepage, with no
            rating grade anywhere in the payload.
      Full investigation notes: [CRA_ENDPOINTS.md](CRA_ENDPOINTS.md).
- [x] **Coverage enrichment for `multi_cra`** - the flag is invisible in the
      feeds: measured on live data, cross-feed overlap between agencies was
      exactly **zero**, because each publishes about a different set of companies.
      Coverage is a per-company question, so `_enrich_coverage` asks CARE directly
      about the top 15 unblocked candidates (concurrency 4, depth capped at 40 on
      the endpoint) and rescores them. This is what turned the queue from 0
      workable leads to its first real one.
- [ ] **Surveillance-date extractor** from rating rationale PDFs → the renewal
      calendar. Each agency formats differently; likely LLM extraction, covered by
      the TokenRouter fallback. A warm list built once and mined forever.
- [x] **Credit screen on top of winnability** - non-negotiable, and enforced:
      `credit_screen()` blocks speculative-grade issuers and anything whose
      sources came back `unverified`. `blocked` and `suppressed` are separate
      states so the UI cannot conflate "must not call yet" with "not worth a call".
- [ ] **Maturing-NCD refinance window** detector from the BSE scrip master we
      already cache.
- [ ] **Merge the signal layer with the `unaccepted-ratings` repo** - both projects
      are solving the same problem twice today.
- [ ] **Entity resolution** - joining MCA / NSE / BSE / CRA names is genuinely hard.
      Budget real time for it and show match confidence in the UI. Two bad matches
      in a demo and BD stops trusting everything.

## How fresh is the data?

Worth knowing before anyone reads a number off the dashboard. There is **no
background job**: everything happens on request.

| Source | Fetched | Cached | What it holds |
|---|---|---|---|
| Acuite | live, on request | 15 min | page 1 of live ratings, ~78 rows |
| Brickwork | live, on request | 15 min | homepage rationale feed, ~45 rows |
| India Ratings | live, on request | 15 min | latest ~10 actions |
| CARE | per company, on demand | 6 hours | that company's current rating book |

So the first request after the cache expires is genuinely real-time and takes
about 6 seconds; requests inside the window are instant and reuse it. CARE is
held far longer because a company's own rating book moves on a surveillance
cycle, not hourly.

**The `days` parameter filters locally, it does not ask for more history.** These
feeds are latest-page snapshots, so `days=365` returns the same rows as
`days=60`. The queue can only ever see roughly the last few weeks of actions.
Real historical depth would need either per-company crawling or storing each
day's snapshot, and neither is built.

- [ ] **Persist a daily snapshot of the feeds** - **FREE** - the cheapest route to
      real history: append each day's actions to Supabase and the queue stops
      being limited to whatever is on page 1 today. Also the prerequisite for
      anything trend-shaped ("three withdrawals this quarter").

## Phase 3 - Dashboard, logins, saved lists

- [x] **Ranked queue dashboard** - built. `GET /api/queue` (backed by
      `backend/pipeline/lead_queue.py`) + `frontend/src/components/QueuePage.jsx`,
      now the default tab. One batch pass over the CRA feeds, grouped by issuer
      and scored - no per-lead network call. Blocked leads always sort last, and
      the page shows a coverage banner naming which agencies were actually read,
      so a short queue can never be mistaken for a quiet week.
      **Live result today: 54 leads, 1 workable, 26 blocked** (was 43/0/23 before
      India Ratings and the CARE enrichment landed). The one workable lead is
      Berar Finance Ltd - INC-tagged at CARE with four withdrawn instruments, and
      freshly rated BBB+ by India Ratings: a company that left one agency for
      another, investment grade, so the credit screen passes it. Verified by hand
      against both agencies' live data. An unreachable backend renders as an
      error, never as an empty queue.
- [ ] **A login per BD user** - **FREE** - Supabase Auth (email or Google). Already
      a dependency, so this is configuration, not a build.
- [ ] **Saved leads** - **FREE** - a `saved_leads` table with row-level security so
      each user reads and writes only their own rows. One Add button writes one row
      and drops the lead into that user's pipeline at stage Identified.
- [ ] **Pipeline tab** - lead lifecycle (Identified → Contacted → Meeting → Proposal
      → Mandated / Lost), owner, notes, follow-up dates, source tagging.
- [ ] **De-duplicate leads across searches**, keyed on CIN.
- [ ] **Briefing surface** - **FREE** - news attached to names already in the
      pipeline, not a global feed. Add BusinessLine, Moneycontrol and Business
      Standard to the existing RSS parser. This prepares the call; it does not rank
      the pipeline.
- [ ] **Add ACER as the 8th agency** so we stop pitching our own clients.
      **Blocked: ACER must provide the rating book. No public source has it.**
- [ ] ~~Weekly digest email~~ - **PARKED, not cancelled.** Everything it needs (the
      ranked queue, per-user identity, the send list) falls out of the dashboard
      work above, so building the dashboard first costs the email nothing.

## Phase 4 - Universe and sizing

- [ ] **Ingest the MCA Company Master** - **FREE** - corrects the retired V2 note
      that called this paid. Source: <https://www.data.gov.in/catalog/company-master-data>,
      CSV/ZIP per Registrar of Companies. Carries CIN, name, status, class,
      authorized and paid-up capital, registration date, state, RoC, principal
      business activity, registered office address. Replaces the OpenStreetMap
      guesswork that currently serves the **Corporates** segment in
      `backend/pipeline/discovery.py`.
      - [ ] Ingest command alongside `backend/registry/ingest.py`
      - [ ] Geocode registered addresses once at ingest, not per search
      - [ ] Point the Corporate branch of `discovery.py` at the registry
      - [ ] Keep OSM as a fallback only, and label the source in the response
- [ ] **Automate the monthly registry refresh** - **FREE** - currently a manual CLI
      run, so the registry silently ages.
- [ ] **Fundamentals for sizing** - **FREE tier** - Twelve Data (800 req/day, NSE
      coverage) or Alpha Vantage (`.BSE` symbols) into `fit_analyzer.py`, which today
      scores on entity type, CIN and instrument count with zero financials. Adds debt
      quantum and interest coverage, so a Rs 5,000 cr borrower stops looking like a
      Rs 50 cr one.
- [ ] **Pitch-brief PDF export** - one page per company for meetings.

## Known gaps we are accepting for now

- [ ] **Decision-maker contacts** - **PAID, no free option.** Hunter's free tier is
      25 lookups per *month* (capped at 5/search to protect it) and no free source
      covers Indian mid-cap CFOs. RBI-filed emails are used where they exist. Needs
      a budget decision, not code.
- [ ] **Rating fields** are absent from BSE's scrip master, so the 7-agency matrix
      rests on NSE disclosures alone. The Phase 2 CRA scrapers are the fix.
- [ ] **Issue date and issue size** are unavailable from the master list; coupon and
      maturity are parsed from BSE's scrip-id convention and return blanks rather
      than guessing.
- [ ] **`_guess_entity_type`** is a name-only heuristic. Deliberately left: it runs
      in one place, only when the registry has no answer, and only sets a display
      label. The CIN's NIC code would be better but the code ranges need verifying.
- [ ] **Live CIN → name resolution** - **PAID, skippable.** Probe42 / Tofler. Only
      needed for real-time company status; discovery does not need it.
- [x] ~~**Headless browser**~~ - **not needed, and the cheaper path won.** The
      "look for the internal JSON API first" bet paid off: India Ratings and CARE
      both turned out to have plain, replayable JSON endpoints needing no cookies
      or session, taking coverage from 2 of 7 to 4 of 7 over plain `httpx`. Of the
      three still missing, a browser would only help ICRA and Infomerics - CRISIL
      sits behind a reCAPTCHA and is off-limits by policy regardless. That is no
      longer worth buying browser infrastructure for. Revisit only if ICRA
      specifically becomes load-bearing.
- [ ] **Bus factor of one.** One maintainer for seven scrapers. Worth deciding now
      what happens when you are unavailable.
- [ ] **Regulatory specifics unverified** - the RBI risk-weight and MSME-scheme
      points behind the Phase 0 empanelment case are directionally right but not
      confirmed. Check before committing spend or headcount.

---

## Done (2026-09-04)

- [x] **The backend URL renders the app.** `https://acer-iq.onrender.com/`
      answered `{"detail":"Not Found"}` and its only human-readable page was
      `/docs` (Swagger), so opening the API host showed a guide instead of the
      product. `/` now serves `frontend/dist` when a build is present, and
      otherwise redirects to `FRONTEND_URL` - deep links preserved. Liveness
      moved to `/api/health` so `/` stays free for the UI.

- [x] **BSE data path restored.** Both debt *search* endpoints were retired by
      BSE (302 to `error_Bse.html`), silently emptying past instruments, CIN,
      directors, addresses and autocomplete. Replaced with the still-live
      active-scrip master, cached 12h: **one** BSE call per search instead of
      sixty. 27/60 Mumbai NBFC leads now carry real instruments; zero did before.
- [x] **Failures are visible.** Structured logging; `_safe()` logs and tallies
      every failure; `sources[]` health block on `/api/search` and
      `/api/company-credit`, badged in both the Directory and Company Research
      tabs. An empty result can no longer pass as a fact.
- [x] **LLM fallback chain.** OpenRouter → TokenRouter, so a dead key or a
      rate-limited free tier degrades to the next provider instead of silently
      dropping to rule-based scores. Truncated replies are rejected and retried
      on the next provider.
- [x] **Incorporation date** read from the CIN, not BSE's listing date (Tata
      Capital: incorporated 1991, listed 2025 - the old code said 2025).
- [x] **Unresolvable CIN** 404s with a reason instead of reporting "rated by
      nobody".
- [x] **Issuer matching is exact-only** - removed prefix matching that gave a
      parent's debt and CIN to subsidiaries ("REC Power Development" inheriting
      REC Ltd's bonds). Registry `(Formerly known as …)` noise is stripped
      instead, which is what makes the exact match land.
- [x] **Hunter quota protected** - capped at 5 lookups per search.
- [x] **CORS** env-driven; dead config keys removed with `extra = "ignore"` so
      leftover Render env vars cannot stop the app booting.
- [x] **Tests + CI** - 14 checks, no network, wired to GitHub Actions with a
      frontend build and an app-import smoke test.
