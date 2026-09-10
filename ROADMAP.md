> **Superseded.** This is the June 2026 framing: the problem was the data.
> Kept for history. The current plan is [ROADMAP_V3.md](ROADMAP_V3.md); the
> three framings side by side are in `roadmap-evolution.html`.

# ACER-IQ - Pain Points, Requirements & Solution Roadmap

> Living document. Created 2026-06-10 after a full code review of the backend pipeline,
> frontend, and deployment setup. This is the source of truth for **why** we are
> rebuilding parts of the tool and **in what order**.

---

## 1. What the product is

ACER-IQ is a B2B lead-generation and company-research tool for **ACER**, a
SEBI-registered credit rating agency in India. It serves the BD/sales team with
two modules:

| Module | What it does today |
|--------|-------------------|
| **Find Leads** | City/pincode + entity type (Bank/NBFC/Corporate) + instrument → discovers companies via OpenStreetMap/Overpass, enriches with BSE CorpInfo (CIN, directors), BSE debt instruments, Hunter.io contacts, Google Places offices, then scores 0-100 (rule-based + optional LLM) |
| **Company Research** | Company name/CIN → 7-agency rating coverage matrix from BSE debt data + AI "fit analysis" (first-time mandate / second opinion / renewal) |

**Stack:** FastAPI (async) · React + Vite + Tailwind + Leaflet · Supabase (optional) ·
OpenRouter free LLM · deployed on Vercel (frontend) + Render/Railway (backend).

---

## 2. Pain points (from code review, ranked by severity)

### P1 - Discovery is built on the wrong data source ❗ CRITICAL
- Lead discovery scrapes **OpenStreetMap/Overpass** with name-regex patterns
  (`"Finance Ltd|Capital Ltd|..."`).
- OSM has near-zero coverage of Indian NBFCs/corporates outside metros; anything
  not literally named "X Finance Ltd" on the map is invisible.
- Result: a search for "NBFCs in Jaipur" returns a handful of random map pins
  instead of the **complete universe** of registered NBFCs in Rajasthan.
- Git history shows repeated firefighting here (query widening, blocklists,
  branch filters) - symptoms of the wrong foundation, not fixable by more regex.

### P2 - Silent failure everywhere ❗ CRITICAL
- Nearly every pipeline function swallows errors: bare `except Exception: pass`
  and the `_safe()` wrapper in `backend/main.py`.
- Zero logging in the entire backend.
- When BSE renames a field, blocks our IP, or Hunter quota runs out, data
  silently comes back empty - the user sees "no instruments found" and trusts it.
- **A lead tool that silently shows incomplete data is worse than no tool**,
  because the sales team makes decisions on it.

### P3 - Credit-history data is thinner than the UI implies
- The 7-agency rating matrix depends entirely on BSE's debt-search API returning
  `RATING_AGENCY` / `CREDIT_RATING` fields - which are frequently empty.
- The authoritative source of "who rates whom" - **agency press releases and
  SEBI-mandated disclosures** - is never fetched (only linked in
  `credit_history.py`).
- "Not rated by anyone" today often means "BSE didn't tell us", shown as fact.

### P4 - Rate-limit / quota landmines
- One search = ~30 parallel enrichment chains → **~90+ concurrent BSE API hits**
  (ban risk), up to 30 Hunter calls (free tier = 25/**month**), up to 30 LLM
  calls on a rate-limited free OpenRouter model.
- No caching of any external call - the same company gets re-fetched on every search.

### P5 - No security, no persistence
- CORS `allow_origins=["*"]`, zero authentication - anyone with the URL can burn
  our API quotas.
- `_search_cache` is in-memory only; dies on every Render free-tier sleep, so
  CSV export links break.

### P6 - Known bugs & inconsistencies
- [ ] `scorer.py` hardcodes `age = 2025 - year` (it's 2026; use current year).
- [ ] BSE **listing date** is presented as **incorporation date** (`mca_scraper.py`).
- [ ] `/api/company-credit` with a CIN passes the CIN into a *name*-search endpoint - almost always fails.
- [ ] Branding chaos: "LeadRadar" (UI/README) vs "CredSight" (`llm.py` headers) vs "ACER-IQ" (repo) vs "Infomerics" (prompts). Pick one.
- [ ] `config.py` still carries unused `anthropic_api_key` / `google_maps_api_key` split.
- [ ] Hunter enrichment is useless in practice: OSM rarely has `website`, and without a domain Hunter returns nothing.

### P7 - No tests, no CI
- Every BSE schema change is discovered in production by a confused salesperson.

---

## 3. Requirements (what "next level" means)

### Functional
1. **Complete lead universe** - searching a city/state must return *every*
   registered NBFC / bank / active company there, not whatever OSM knows.
2. **Trustworthy data** - every datapoint shows its source + freshness; failures
   are visible ("BSE unavailable"), never silent blanks.
3. **Actionable intelligence** - surface *events* that create mandates: rating
   withdrawals, INC (issuer-not-cooperating) flags, downgrades, new debt
   issuance announcements, upcoming surveillance renewals.
4. **Workflow** - leads have a lifecycle (New → Contacted → Meeting → Won/Lost),
   owners, notes, and saved searches; weekly digest.

### Non-functional
5. Search results in < 3 s for discovery (DB query), enrichment may stream in.
6. Survive free-tier restarts - all state in Postgres/Supabase, not memory.
7. Basic auth (the tool is internal to ACER's sales team).
8. External calls cached + rate-limited; LLM scoring batched (1 call per search, not 30).
9. Structured logging; parser tests for every external API we depend on.

---

## 4. Our approach - four phases, in this order

### Phase 1 - Registry-backed discovery (fixes P1) ← **START HERE**
Replace map-scraping with the structured public registries where the lead
universe actually lives:

| Source | What it gives us | Format |
|--------|------------------|--------|
| RBI - List of registered NBFCs | ~9,000 NBFCs: name, address, region, layer (Base/Upper) | XLSX, published on rbi.org.in |
| RBI - Bank lists (SCBs, UCBs, RRBs, SFBs) | Complete bank universe incl. cooperative banks (ACER's actual bank targets) | XLSX/PDF |
| MCA - Company master data | CIN, registered address, state, paid-up capital, status, class | State-wise CSV |
| BSE/NSE debt listings | Active debt issuers | API/CSV |

**Build:**
- `companies` table in Supabase/Postgres + one-time ingestion scripts + periodic refresh job.
- Geocode registered addresses once at ingest (cached), not per search.
- `/api/search` becomes a fast DB query (state/city/entity/capital filters);
  live scrapers (BSE, Zauba, Hunter) demoted to **on-demand enrichment** of a
  selected lead, not discovery.
- Map pins from stored coordinates; UI unchanged.

**Outcome:** complete, instant, deterministic lead coverage. The single biggest jump in product value.

### Phase 2 - Engineering hardening (fixes P2, P4, P5, P6, P7)
- Structured logging (`structlog`/std logging) + per-source status surfaced in
  API responses and UI badges ("BSE ✓ · Hunter ✗ quota").
- Cache layer (Postgres table or simple TTL cache) for BSE/geocode/LLM results.
- `asyncio.Semaphore` rate limiting per external host.
- Background search jobs with progress streaming (SSE/polling: "enriching 12/30…").
- Simple auth (single shared token or Supabase Auth), lock down CORS.
- Batch LLM scoring: one prompt scoring all companies in a search.
- Fix all P6 bugs; settle branding (one name everywhere).
- pytest suite for BSE/Zauba parsers (recorded fixtures) + GitHub Actions CI.

### Phase 3 - Pipeline Radar module (fixes P3, delivers Req. 3) - **the flagship**
Full spec: **[PIPELINE_RADAR_SPEC.md](PIPELINE_RADAR_SPEC.md)**. New third tab:
companies expected to raise money in coming month/quarters, caught *before*
they mandate an agency.
- Auto-rolling FY-quarter horizon chips (This month / FY27 Q2 / FY27 Q3 …) -
  signals stored with absolute dates, labels computed at request time.
- Signal engine: board-meeting fundraise intimations + special resolutions
  (BSE/NSE), maturing NCD refinance windows, SEBI filings, expansion/capex news
  with source links, new NBFC licences, competitor withdrawals/INC.
- Sector outlook cards: bullish/cautious stance per sector with cited sources,
  regenerated weekly.
- Company drill-down: management, signal timeline with evidence, 7-agency
  matrix, debt maturity ladder, recommended pitch.
- Every lead carries a "why now" reason + clickable source. No naked scores.

### Phase 4 - Workflow (delivers Req. 4)
- Lead status pipeline, owner assignment, notes, saved searches, search history
  UI (Supabase already wired).
- De-duplication of leads across searches (keyed on CIN).
- Weekly digest email. (CRM integrations explicitly out of scope for now.)

---

## 5. Sequencing rationale

```
Phase 1 (foundation: complete data)
   → Phase 2 (reliability: trust the data)
      → Phase 3 (differentiation: intelligence nobody else gives sales)
         → Phase 4 (stickiness: the team lives in it)
```

Phases 3 and 4 are only worth building once the underlying company universe
(Phase 1) and reliability (Phase 2) exist - otherwise we'd be adding features
on top of incomplete, silently-failing data.

## 6. Status (updated 2026-09-04)

Actionable task list: **[TODO.md](TODO.md)**
Visual roadmap: **[ROADMAP_FLOW.html](ROADMAP_FLOW.html)** (source: `roadmap-flow.workflow.json`)

**Decision: complete the existing two modules first (Find Leads + Company
Research) - i.e. Phase 1 + Phase 2. Pipeline Radar is fully specced in
[PIPELINE_RADAR_SPEC.md](PIPELINE_RADAR_SPEC.md) but ON HOLD until then.**

- [ ] Phase 1 - Registry-backed discovery ← **IN PROGRESS** (RBI/NSE done; MCA Corporate segment outstanding)
- [ ] Phase 2 - Engineering hardening ← **IN PROGRESS**
- [ ] Phase 3 - Pipeline Radar module - **ON HOLD (spec ready)**
- [ ] Phase 4 - Workflow - on hold

### Done 2026-09-04

**P0 - BSE data path was silently dead, now restored.** BSE retired both debt
*search* endpoints (`GetDebtScripsSearchData/w` and `SearchData/w` now 302 to
`error_Bse.html`). Every BSE-derived field - past instruments, CIN, directors,
registered address, company autocomplete - was coming back empty and the UI
presented that as fact. Replaced with the still-live active-scrip master
(`ListofScripData/w`), fetched once per 12h and indexed by normalized issuer
name: 676 debt issuers / 6,272 debt scrips / 6,364 listed names. A 60-lead
search now makes **one** BSE call instead of sixty, and 27/60 Mumbai NBFC leads
come back with real instruments where previously zero did.

- [x] P2 - Structured logging across backend; `_safe()` logs and tallies every
      failure instead of swallowing it. `database.py` no longer hides
      persistence errors.
- [x] P2 - Per-source health surfaced in `/api/search` and `/api/company-credit`
      responses (`sources[]`) and badged in the UI
      (`frontend/src/components/SourceHealth.jsx`). An empty result can no
      longer masquerade as "this company has no rated debt".
- [x] P6 - Incorporation date now read from the CIN (chars 8:12), not BSE's
      listing date. (Tata Capital: incorporated 1991, listed 2025 - the old
      code reported 2025.)
- [x] P6 - `/api/company-credit` with an unresolvable CIN returns 404 with a
      reason instead of a name-searching a CIN and reporting "rated by nobody".
- [x] P6 - Branding: `CredSight` → `ACER-IQ` in LLM headers. Dead
      `anthropic_api_key` / `google_maps_api_key` config keys removed
      (`extra = "ignore"` added so leftover Render env vars can't break boot).
- [x] P6 - Stale "Run with Anthropic API key" copy → OpenRouter.
- [x] P5 - CORS is env-driven (`ALLOWED_ORIGINS`), defaulting to a
      `*.vercel.app` + localhost regex instead of `*`.
- [x] P7 - Tests + CI: `backend/test_hardening.py` (12 checks, `python -m
      backend.test_hardening`, no network) covering failure visibility, issuer
      matching, and BSE scrip-id coupon/maturity parsing. Wired to GitHub
      Actions in `.github/workflows/ci.yml` (backend tests + app-import smoke +
      frontend build).
- [x] P4 - **LLM fallback chain**: `llm.py` tries OpenRouter, then TokenRouter
      (`z-ai/glm-5.3-free`), so a missing key or a rate-limited free tier
      degrades to the next provider instead of silently dropping to rule-based
      scores. Two things a naive drop-in misses: glm-5.3 is a **reasoning
      model** whose private reasoning tokens are billed against `max_tokens`
      (~300 for a small JSON answer), so the callers' 512-600 budget is raised
      to 2,000 or the JSON truncates mid-object; and a reply cut off at
      `max_tokens` is **rejected and retried on the next provider**, because
      half-parsed JSON is worse than none. Keys are per-environment; the
      fallback key lives in `.env` only, never in the repo.
- [x] P4 - Hunter's free tier is 25 lookups/**month**, so contact enrichment is
      capped at 5 per search (`MAX_HUNTER_LOOKUPS`) and logs when it stops. It
      already skipped companies with no website; failures are now logged.
- [x] Per-source badges reach **Company Research** too, not just the directory -
      that is the screen the sales team trusts most.
- [x] `.env.example` documents `ALLOWED_ORIGINS` and `LOG_LEVEL`; Supabase's
      entry now says what breaks without it.

### Stale entries corrected

Two P6 items no longer describe the code:

- **"Overpass hard-caps at 15 results"** - it does not. The 15 is a slice on
  *Google Places* results (`discovery.py:383`); registry-backed searches return
  up to 60 and the widening trigger is `< 8` candidates.
- **"Hunter enrichment is useless in practice"** - it already returns early when
  a company has no website, so it does not waste quota. The limitation is that
  registry rows rarely carry a domain, which is a data gap, not a bug.

### Next, in order

1. **Phase 1 finish - MCA Company Master ingest (FREE).** The assumption in
   §"Known challenges" that MCA data costs money is **wrong**: the full company
   master is a free public download from
   [data.gov.in](https://www.data.gov.in/catalog/company-master-data), split per
   Registrar of Companies as CSV/ZIP, carrying CIN, name, status, class,
   authorized/paid-up capital, registration date, state, RoC, principal business
   activity and registered address. That is exactly the Corporate segment
   `discovery.py` currently fakes with OpenStreetMap. Paid vendors are only
   needed for *real-time* status and directors, which discovery does not need.
2. **Phase 2 finish - caching, auth, persistence.** Supabase persistence on
   every search (so CSV export survives a restart), a shared-token auth gate,
   and a TTL cache for geocode/MCA results.
3. **Always-on hosting.** The free Render dyno sleeps, which is what makes
   `_search_cache` loss and export 404s a recurring problem. This is the one
   unavoidable recurring cost.
4. **Phase 3 - Pipeline Radar.** Per-CRA press-release scrapers for
   withdrawals, INC flags and surveillance dates. Free, but needs one scraper
   per agency plus PDF parsing.

### Known remaining gaps

- BSE's scrip master carries **no rating agency / credit rating fields**, so the
  7-agency matrix still rests on NSE disclosures and announcements only
  (`credit_history.py`, `nse_ratings.py`). P3 is unchanged.
- Coupon and maturity are parsed from BSE's scrip-id convention
  (`805BFL26` → 8.05% due 2026), which is a heuristic - it returns blanks for
  commercial papers and odd ids rather than guessing.
- Issue date and issue size are not available from the master list.
- **Issuer matching is exact-only, by design.** Prefix/containment matching was
  tried and removed: it attributed a parent's debt and CIN to a subsidiary
  ("REC Power Development" inheriting REC Ltd's bonds, "Bajaj" resolving to
  Bajaj Global). Showing one company's instruments under another is worse than
  showing none. Registry names are cleaned of `(Formerly known as ...)` noise
  first, which is what makes the exact match land - do not reintroduce fuzzy
  matching to raise coverage. Measured: 27/60 Mumbai NBFCs either way.
- Contact enrichment remains the weakest link: Hunter's free tier is 25
  lookups/month and no free source covers Indian mid-cap CFOs.
- `_guess_entity_type` (`main.py`) is still a name-only heuristic. Left alone
  deliberately: it runs in exactly one place, only when the 12.8k-entity
  registry has no answer, and only sets a display label. The CIN's 5-digit NIC
  code would be more reliable, but mapping NIC ranges to Bank/NBFC/Corporate
  needs verifying against the real code list before it is worth the risk of
  mislabelling.
- **No auth.** Deliberately not added in this pass: a token gate breaks the
  Vercel frontend until the token is wired into it, so it needs to ship as one
  coordinated change, not a silent backend edit.
