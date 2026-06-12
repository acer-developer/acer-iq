# ACER-IQ — Pain Points, Requirements & Solution Roadmap

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
| **Find Leads** | City/pincode + entity type (Bank/NBFC/Corporate) + instrument → discovers companies via OpenStreetMap/Overpass, enriches with BSE CorpInfo (CIN, directors), BSE debt instruments, Hunter.io contacts, Google Places offices, then scores 0–100 (rule-based + optional LLM) |
| **Company Research** | Company name/CIN → 7-agency rating coverage matrix from BSE debt data + AI "fit analysis" (first-time mandate / second opinion / renewal) |

**Stack:** FastAPI (async) · React + Vite + Tailwind + Leaflet · Supabase (optional) ·
OpenRouter free LLM · deployed on Vercel (frontend) + Render/Railway (backend).

---

## 2. Pain points (from code review, ranked by severity)

### P1 — Discovery is built on the wrong data source ❗ CRITICAL
- Lead discovery scrapes **OpenStreetMap/Overpass** with name-regex patterns
  (`"Finance Ltd|Capital Ltd|..."`).
- OSM has near-zero coverage of Indian NBFCs/corporates outside metros; anything
  not literally named "X Finance Ltd" on the map is invisible.
- Result: a search for "NBFCs in Jaipur" returns a handful of random map pins
  instead of the **complete universe** of registered NBFCs in Rajasthan.
- Git history shows repeated firefighting here (query widening, blocklists,
  branch filters) — symptoms of the wrong foundation, not fixable by more regex.

### P2 — Silent failure everywhere ❗ CRITICAL
- Nearly every pipeline function swallows errors: bare `except Exception: pass`
  and the `_safe()` wrapper in `backend/main.py`.
- Zero logging in the entire backend.
- When BSE renames a field, blocks our IP, or Hunter quota runs out, data
  silently comes back empty — the user sees "no instruments found" and trusts it.
- **A lead tool that silently shows incomplete data is worse than no tool**,
  because the sales team makes decisions on it.

### P3 — Credit-history data is thinner than the UI implies
- The 7-agency rating matrix depends entirely on BSE's debt-search API returning
  `RATING_AGENCY` / `CREDIT_RATING` fields — which are frequently empty.
- The authoritative source of "who rates whom" — **agency press releases and
  SEBI-mandated disclosures** — is never fetched (only linked in
  `credit_history.py`).
- "Not rated by anyone" today often means "BSE didn't tell us", shown as fact.

### P4 — Rate-limit / quota landmines
- One search = ~30 parallel enrichment chains → **~90+ concurrent BSE API hits**
  (ban risk), up to 30 Hunter calls (free tier = 25/**month**), up to 30 LLM
  calls on a rate-limited free OpenRouter model.
- No caching of any external call — the same company gets re-fetched on every search.

### P5 — No security, no persistence
- CORS `allow_origins=["*"]`, zero authentication — anyone with the URL can burn
  our API quotas.
- `_search_cache` is in-memory only; dies on every Render free-tier sleep, so
  CSV export links break.

### P6 — Known bugs & inconsistencies
- [x] `scorer.py` hardcodes `age = 2025 - year` → now uses `date.today().year`.
- [x] BSE **listing date** is presented as **incorporation date** → split into a
      separate `listing_date` field end-to-end (API, CSV, UI label "BSE listed").
- [x] `/api/company-credit` with a CIN passes the CIN into a *name*-search endpoint →
      CIN now resolves via the RBI registry (instant) or Zauba, then BSE is searched by name.
- [x] Branding chaos → **ACER-IQ** everywhere (UI title, sidebar, README, LLM headers);
      "Infomerics" remains only as the factual agency name in the 7-agency matrix.
- [x] `config.py` unused `anthropic_api_key` / `google_maps_api_key` removed.
- [x] Hunter enrichment skipped (with visible "skipped" status) when no domain/key —
      the RBI-filed NBFC email is now the primary contact instead.
- [x] **(found by tests)** `geo.state_from_cin` read chars 8-9 instead of 7-8 —
      registry state coverage was 84%, now 99.8% after rebuild.

### P7 — No tests, no CI
- Every BSE schema change is discovered in production by a confused salesperson.

---

## 3. Requirements (what "next level" means)

### Functional
1. **Complete lead universe** — searching a city/state must return *every*
   registered NBFC / bank / active company there, not whatever OSM knows.
2. **Trustworthy data** — every datapoint shows its source + freshness; failures
   are visible ("BSE unavailable"), never silent blanks.
3. **Actionable intelligence** — surface *events* that create mandates: rating
   withdrawals, INC (issuer-not-cooperating) flags, downgrades, new debt
   issuance announcements, upcoming surveillance renewals.
4. **Workflow** — leads have a lifecycle (New → Contacted → Meeting → Won/Lost),
   owners, notes, and saved searches; weekly digest.

### Non-functional
5. Search results in < 3 s for discovery (DB query), enrichment may stream in.
6. Survive free-tier restarts — all state in Postgres/Supabase, not memory.
7. Basic auth (the tool is internal to ACER's sales team).
8. External calls cached + rate-limited; LLM scoring batched (1 call per search, not 30).
9. Structured logging; parser tests for every external API we depend on.

---

## 4. Our approach — four phases, in this order

### Phase 1 — Registry-backed discovery (fixes P1) ← **START HERE**
Replace map-scraping with the structured public registries where the lead
universe actually lives:

| Source | What it gives us | Format |
|--------|------------------|--------|
| RBI — List of registered NBFCs | ~9,000 NBFCs: name, address, region, layer (Base/Upper) | XLSX, published on rbi.org.in |
| RBI — Bank lists (SCBs, UCBs, RRBs, SFBs) | Complete bank universe incl. cooperative banks (ACER's actual bank targets) | XLSX/PDF |
| MCA — Company master data | CIN, registered address, state, paid-up capital, status, class | State-wise CSV |
| BSE/NSE debt listings | Active debt issuers | API/CSV |

**Build:**
- `companies` table in Supabase/Postgres + one-time ingestion scripts + periodic refresh job.
- Geocode registered addresses once at ingest (cached), not per search.
- `/api/search` becomes a fast DB query (state/city/entity/capital filters);
  live scrapers (BSE, Zauba, Hunter) demoted to **on-demand enrichment** of a
  selected lead, not discovery.
- Map pins from stored coordinates; UI unchanged.

**Outcome:** complete, instant, deterministic lead coverage. The single biggest jump in product value.

### Phase 2 — Engineering hardening (fixes P2, P4, P5, P6, P7)
- Structured logging (`structlog`/std logging) + per-source status surfaced in
  API responses and UI badges ("BSE ✓ · Hunter ✗ quota").
- Cache layer (Postgres table or simple TTL cache) for BSE/geocode/LLM results.
- `asyncio.Semaphore` rate limiting per external host.
- Background search jobs with progress streaming (SSE/polling: "enriching 12/30…").
- Simple auth (single shared token or Supabase Auth), lock down CORS.
- Batch LLM scoring: one prompt scoring all companies in a search.
- Fix all P6 bugs; settle branding (one name everywhere).
- pytest suite for BSE/Zauba parsers (recorded fixtures) + GitHub Actions CI.

### Phase 3 — Pipeline Radar module (fixes P3, delivers Req. 3) — **the flagship**
Full spec: **[PIPELINE_RADAR_SPEC.md](PIPELINE_RADAR_SPEC.md)**. New third tab:
companies expected to raise money in coming month/quarters, caught *before*
they mandate an agency.
- Auto-rolling FY-quarter horizon chips (This month / FY27 Q2 / FY27 Q3 …) —
  signals stored with absolute dates, labels computed at request time.
- Signal engine: board-meeting fundraise intimations + special resolutions
  (BSE/NSE), maturing NCD refinance windows, SEBI filings, expansion/capex news
  with source links, new NBFC licences, competitor withdrawals/INC.
- Sector outlook cards: bullish/cautious stance per sector with cited sources,
  regenerated weekly.
- Company drill-down: management, signal timeline with evidence, 7-agency
  matrix, debt maturity ladder, recommended pitch.
- Every lead carries a "why now" reason + clickable source. No naked scores.

### Phase 4 — Workflow (delivers Req. 4)
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
(Phase 1) and reliability (Phase 2) exist — otherwise we'd be adding features
on top of incomplete, silently-failing data.

## 6. Status (updated 2026-06-10, end of day)

**Decision: complete the existing two modules first (Find Leads + Company
Research) — i.e. Phase 1 + Phase 2. Pipeline Radar is fully specced in
[PIPELINE_RADAR_SPEC.md](PIPELINE_RADAR_SPEC.md) but ON HOLD until then.**

- [x] Phase 1 — Registry-backed discovery — **DONE.** Committed
      `registry.sqlite` (10,552 head offices: 9,075 NBFCs + 27 ARCs from the
      RBI XLSX, 1,439 UCBs from the two RBI PDFs, 11 SFBs), ingest + query
      layer, `/api/search` serves Banks/NBFCs from the registry instantly;
      OSM/Places remains only as the Corporates fallback. RBI-filed NBFC
      email is the first contact on every lead.
- [x] Phase 2 — Engineering hardening — **DONE** (see [HANDOFF.md](HANDOFF.md)):
      structured logging, per-source status in `/api/search` +
      `/api/company-credit` responses, TTL cache + per-source semaphores for
      all external calls (`backend/netutil.py`), batch LLM scoring (1 call
      per search), all P6 bugs fixed, pytest suite (31 tests, fixtures) +
      GitHub Actions CI, CORS origins configurable via `CORS_ORIGINS`.
      *Deferred from the Phase 2 list:* auth, background search jobs with
      progress streaming, persistent (Postgres-backed) cache — tracked below.
- [ ] Phase 2 leftovers — simple auth, SSE/polling progress streaming,
      cache persistence across restarts.
- [ ] Phase 3 — Pipeline Radar module — **ON HOLD (spec ready)**
- [ ] Phase 4 — Workflow — on hold
