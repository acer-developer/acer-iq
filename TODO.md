# ACER-IQ - TODO

Live status as of **2026-09-10**, ordered to match [ROADMAP_V3.md](ROADMAP_V3.md).
The rationale for the ordering - and the reframe from "find leads" to "rank on
winnability" - is in that document. The three roadmap framings side by side are
in `roadmap-evolution.html`.

Legend: **FREE** = no new spend · **PAID** = costs money · **DECISION** = needs your call.

## Where this actually stands

**Working end to end:** CRA sites -> scored, ranked, credit-screened queue on
screen. 4 of 7 agencies readable. Plus a renewal calendar, a maturing-debt
refinance list (353 issuers, the biggest lead source in the app), a growing
local archive of every feed fetch, and per-company briefing news.
56 tests, seven module self-checks. All offline except one, which needs an LLM
key set to pass - see the note at the foot of this file.

**Not done, and honest about why:**

| Gap | Blocked on |
|---|---|
| Logins (per-user lists) | a Supabase project. **There isn't one** - every `.env` in the tree still has the README's `your_url_here` placeholders. Saved leads now run on SQLite instead, as one shared team list. |
| Search persistence (`/api/export/{id}`) | same. `_search_cache` is still in-process. |
| Renewal calendar (surveillance dates) | rationale-PDF parsing per agency. Multi-session. |
| MCA first-timer join | bulk registry ingest + geocoding. Multi-session. |
| Always-on hosting | your call: Oracle Cloud Always Free, or ~$7/mo. |
| Outcome-driven scoring weights | six months of outcome data that does not exist yet. |

**The live number, so it is not oversold:** 54 leads, 1 workable, 26 blocked.
That is the credit screen working, not a bug - Acuite and Brickwork's published
book is mostly sub-investment-grade MSME paper. Volume has to come from the
renewal calendar and the MCA join, both still ahead.

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
- [x] **Outcome logging schema** - built in `backend/pipeline/pipeline_store.py`
      on stdlib sqlite3, same pattern as `backend/registry/store.py`. Every save
      and every stage change appends to `lead_events` carrying the flags that
      produced the lead, so it becomes possible to ask later which signal
      actually converts. History is deliberately kept when a lead is removed:
      worked-and-dropped is precisely the outcome data the weights need.
      Read it at `GET /api/leads/events`.
- [ ] **Auth gate + CORS lockdown** - **FREE** - **DECISION**: backend and frontend
      must ship together or the Vercel frontend breaks. Right now anyone with the
      URL can burn our quotas. Supersedes the shared-token idea: use Supabase Auth,
      which Phase 3 needs anyway.
- [x] **Source-health alarms** - `/api/health` now reports all seven CRA scrapers
      (`_cra_status`), returns `status: "degraded"` with a `degraded[]` list when
      any breaker is open, and logs that at WARNING so an always-on host's log
      alerting fires without anything polling the endpoint. Statically blocked
      agencies report ok-with-detail, deliberately: alerting on a known gap
      trains whoever is on call to ignore the endpoint.
- [ ] **Always-on backend** - **FREE or ~$7/mo** - the free Render dyno sleeps,
      which is the root cause of lost caches and export 404s. Oracle Cloud Always
      Free ARM VM makes this cost nothing; ~$7/mo buys less hassle.
- [x] **TTL cache for geocode and MCA lookups** - both were plain dicts that
      never expired or bounded, which on the always-on host below means growing
      for the life of the process and serving a stale CIN forever. Now
      `backend/pipeline/ttl_cache.py` (`TTLCache`), 12h/2000 for MCA and
      24h/1000 for geocode. The centre-of-India geocode fallback is
      deliberately not cached, so a failed lookup is not pinned for a day.
      Not `functools.lru_cache`: these callers are async and need clock-based
      expiry, which lru_cache has no concept of.

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
- [x] **Renewal calendar** - built in `backend/pipeline/renewal.py`, served at
      `GET /api/renewals`. **The premise in this line was wrong, and reading the
      documents is what showed it:** there is no surveillance date in a rating
      rationale. On live Brickwork rationales the only occurrence of the word
      "surveillance" is the boilerplate disclaimer, and there is no validity,
      expiry or next-review field anywhere in the document. India Ratings'
      press-release pages render client-side (78 characters of extractable text),
      so they need a headless browser, which V3 rules out. No LLM can extract a
      date that is not written, so the TokenRouter fallback was never the blocker.
      What the rationale *does* carry is better: its facilities table is headed
      `Previous (19-August-2025)`, the issuer's previous action date. With the
      current action that gives the issuer's **own measured review interval**,
      which beats a published date because it is observed rather than assumed.
      Where no previous date exists, SEBI's at-least-annual rule is the floor.
      Every row therefore carries `basis` - `observed` vs `annual` - because
      these are PREDICTED dates and a computed date that looks published is
      exactly the false precision this codebase keeps refusing to ship.
      Self-check: `python -m backend.pipeline.renewal`.
- [x] **Credit screen on top of winnability** - non-negotiable, and enforced:
      `credit_screen()` blocks speculative-grade issuers and anything whose
      sources came back `unverified`. `blocked` and `suppressed` are separate
      states so the UI cannot conflate "must not call yet" with "not worth a call".
- [x] **Maturing-NCD refinance window** - built in
      `backend/pipeline/refinance.py`, served at `GET /api/refinance`. Costs no
      new network call: the BSE scrip master is already cached 12h for the
      company search. **Live today: 353 issuers with listed debt maturing inside
      a 2-year horizon - 123 this year, 161 next** - which is by some distance
      the largest lead source in the app, against the ranked queue's 54.
      Maturity resolves to a YEAR, never a date: BSE's master has no maturity
      date field, so the year is inferred from the scrip id convention. Scrips
      whose id does not follow it are counted as `unknown_maturity`, never as
      "nothing due". Self-check: `python -m backend.pipeline.refinance`.
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

Measured on the live endpoint: **22.5s on a cold cache, 0.78s warm.** The cold
cost is dominated by the CARE enrichment (15 leads x 2 calls), not the feeds.
CARE is held far longer than the feeds because a company's own rating book
moves on a surveillance cycle, not hourly.

**The `days` parameter filters locally, it does not ask for more history.** The
feeds are latest-page snapshots, so a *live* `days=365` returns the same rows as
`days=60`. That cap is now lifted from the second direction: every fetch is
archived to `snapshots.sqlite` and the queue builds from live **union** archive,
so its depth grows by one day for every day the app is used. It does not
retroactively reach back - the archive starts the day it starts - so read
`archive.depth_days` on `/api/health` before trusting a trend.

- [x] **Daily snapshot of the feeds** - built in
      `backend/pipeline/snapshot_store.py` on stdlib sqlite3, not Supabase, for
      the same reason as `pipeline_store.py`: there is no Supabase project, and
      blocked-on-credentials forever is worse than local now. The row shape is
      deliberately portable if that changes.
      `lead_queue.build_queue` archives on every request and then builds from
      live **union** archive, so history accumulates as a side effect of ordinary
      use with no scheduler to run or forget, and the queue stops being capped at
      whatever sits on page 1 today. Re-archiving is idempotent on
      (agency, company, rating, action, date). Verified against live feeds: with
      the feeds returning nothing, the queue still had 51 actions from the
      archive instead of zero. `/api/history` exposes it and `/api/health`
      reports its depth. Never fatal - an unwritable archive degrades to the live
      feed and says so. Self-check: `python -m backend.pipeline.snapshot_store`.

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
- [ ] **A login per BD user** - **BLOCKED, needs a Supabase project.** Supabase is
      in `requirements.txt` and `backend/database.py`, but was never actually set
      up: every `.env` still carries the README's `your_url_here` placeholders.
      Until then the pipeline is one shared list. Do **not** hand-roll auth for
      this; it is the one part of the app where rolling our own is the wrong call.
- [x] **Saved leads** - on SQLite, as **one shared list for the team**, not
      per-user: row-level security needs auth, and there is no Supabase project.
      The Add button in the queue now really persists, shows Saved once stored,
      and surfaces a failure inline rather than silently pretending. Saving is
      idempotent server-side, so clicking Add on a company already at Proposal
      will not reset it. `GET/POST /api/leads`, `POST /api/leads/{name}/stage`,
      `DELETE /api/leads/{name}`.
- [x] **Pipeline tab - deliberately NOT built.** The backend is done and stays:
      stages Identified, Contacted, Meeting, Proposal, Mandated, Lost, plus notes,
      funnel counts and full event history at `/api/leads`, unknown stages
      refused rather than written. The **UI was built and then removed on ACER's
      instruction: ACER already runs a CRM, and a second pipeline UI inside
      ACER-IQ would be a worse copy of it.** V3 phase 3 listed a pipeline tracker
      because it assumed no CRM existed; that assumption was wrong.
      **The open consequence, stated plainly:** the outcome loop is one of the
      four premortem non-negotiables, and it closes only if stage transitions get
      recorded somewhere. With no tab and no CRM write-back, `lead_events` will
      only ever log `saved`, so the winnability weights stay flat and un-tunable.
      Either the CRM writes outcomes back into `/api/leads`, or that
      non-negotiable stays open - it is not closed by dropping the UI.
- [ ] **De-duplicate leads across searches**, keyed on CIN.
- [x] **Briefing surface** - `GET /api/news?company_name=` filters every source
      to one company, so it prepares a call rather than ranking anything.
      Matched on the suffix-stripped name (`bse_scraper._norm`) so a feed's
      "Bajaj Finance" meets a rating action's "Bajaj Finance Ltd.", by substring
      rather than token overlap - token overlap makes "India Cements" match every
      headline containing the word India, and two bad matches is all it takes for
      BD to stop trusting the tool. Feeds probed live before wiring:
      **BusinessLine works** (companies + markets, ~60 items each),
      **Business Standard works** (companies + markets, ~35 each),
      **Moneycontrol is blocked** - every `/rss/*.xml` route answers HTTP 403 to
      a plain client, so it is deliberately not wired: a feed that fails on every
      request would put a permanent red entry in `sources_fail` and train people
      to ignore it. Six feeds now fetch concurrently rather than in series
      (275 items in 1.6s).
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
