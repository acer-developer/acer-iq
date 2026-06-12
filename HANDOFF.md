# HANDOFF — Phase 1 + Phase 2 completion (2026-06-12)

Night's goal per ROADMAP.md: finish **Phase 1 (registry-backed discovery)**
and **Phase 2 (engineering hardening)** so Find Leads + Company Research work
properly. Pipeline Radar remains ON HOLD. Both phases are done; details and
verification notes below.

---

## What was done

### Phase 1 — Registry-backed discovery

- **`backend/registry/ingest.py`** parses the committed RBI sources in
  `backend/registry/data/` — `rbi_nbfc.xlsx` (9,075 NBFCs + 27 ARCs incl.
  classification, layer, CIN, address, **email**), the two UCB PDFs
  (1,439 cooperative banks, head offices by construction) and 11 hardcoded
  Small Finance Banks — into the committed **`registry.sqlite`**
  (10,552 companies, 99.8% with state, 98% with map coordinates).
  Re-run with `python -m backend.registry.ingest` (uses cached files;
  `--fresh` re-downloads from RBI).
- **`backend/registry/store.py`** is the query layer: `search(city|pincode,
  entity_type)` (ranked: SFB/Scheduled-UCB first, Upper/Middle-layer NBFCs
  first, has-email first), `by_cin()`, `stats()`.
- **`/api/search`** serves Banks and NBFCs from the registry — complete
  universe, head offices only, instant, no network. The old Overpass/Places
  path survives **only** as the Corporates path / registry-miss fallback.
- The RBI-filed NBFC **email becomes the first contact** on every lead
  ("Registered contact / RBI-filed email").
- Registry rows that already carry an RBI-verified CIN **skip** the per-lead
  BSE/Zauba master-data lookup during search (huge call-volume saving).

### Phase 2 — Hardening

- **`backend/netutil.py`** (new): `TTLCache` (negative results cached only
  5 min), per-source `asyncio.Semaphore` limits (bse 4, zauba 2, llm 2,
  geocode 1, hunter 2, places 3, overpass 2), and `SourceStatus` — a
  per-request tally surfaced as **`sources`** in `/api/search` and
  `/api/company-credit` responses
  (`{"bse": {"status": "degraded", "ok": 12, "failed": 3, "detail": …}}`).
- **Structured logging** everywhere (`acer_iq.*` loggers, timestamp|level|
  name|message). Every previously-silent `except: pass` now logs; `_safe()`
  in `main.py` logs with a label.
- **TTL caching** on all external calls: BSE instruments 6h, MCA/Zauba 24h,
  Hunter 24h, LLM by prompt-hash 6h, geocode 7d, Overpass 6h, Places 6h.
  A BSE "no instruments" answer is cached only when BSE actually responded —
  total failures stay uncached so the next request retries.
- **Batch LLM scoring**: one OpenRouter call scores the whole search
  (`scorer.score_companies`, prompt lists up to 40 companies, JSON-array
  response applied via `apply_batch_scores`). Rule-based scores always
  computed first and kept when the LLM is unavailable/unusable.
- **P6 bugs fixed**:
  - 2025 hardcode in scorer (was already fixed → `date.today().year`).
  - BSE listing date is now `listing_date`, never `incorporation_date`
    (model + CSV column "BSE Listed" + UI label "BSE listed").
  - `/api/company-credit` with a CIN: resolved via `registry.by_cin()`
    (instant, covers all RBI NBFCs) or Zauba; BSE searched by *name*.
    Verified live: `L65922RJ2011PLC034297` → "Aavas Financiers Limited".
  - Branding unified to **ACER-IQ** (index.html, App.jsx sidebar, README,
    llm.py headers; agency matrix keeps "Infomerics" as the factual name).
  - `config.py`: removed unused `anthropic_api_key`/`google_maps_api_key`;
    added `CORS_ORIGINS` env (comma-separated; default `*`).
- **Tests + CI**: `backend/tests/` — 31 tests. BSE debt-search and CorpInfo
  parsers run against recorded-shape fixtures in `backend/tests/fixtures/`;
  registry parsers run against the **committed RBI XLSX/PDF themselves**;
  plus store/geo/scorer/netutil/parse_json tests. GitHub Actions workflow
  `.github/workflows/ci.yml` runs them on push/PR.
- **Bug found by the tests**: `geo.state_from_cin` read CIN chars 8-9
  instead of 7-8, so state was missing for ~16% of the registry. Fixed and
  `registry.sqlite` rebuilt — state coverage 84% → **99.8%**.

## Verified locally

- `pytest backend/tests -q` → **31 passed**.
- `uvicorn backend.main:app` →
  - `GET /api/health` → registry available, 10,552 companies.
  - `POST /api/search {"city":"Jaipur","entity_type":"Banks"}` → 35 real
    cooperative-bank head offices + AU Small Finance Bank, all
    `discovery_source: rbi_registry`, with coordinates; `sources` correctly
    showed `bse: failed (HTTP 403)` / `hunter,llm: skipped` (see caveat).
  - NBFCs in Jaipur → 60 leads, CINs + RBI emails as first contacts.
  - Company-credit by CIN → resolves name via registry, fit analysis works.
- `npm run build` in `frontend/` → clean.

## Caveats / what to check next

1. **This sandbox blocks outbound BSE/Zauba/Overpass (HTTP 403 from an egress
   proxy)** — so BSE enrichment, Zauba and the Corporates fallback could not
   be exercised live here. The `sources` field makes this visible per
   request. **Re-test one search and one company-research from a normal
   network** to confirm BSE field names still match the fixtures.
2. The BSE/CorpInfo fixtures are recorded-shape payloads built from the
   parser's known field spellings, not raw captures. When you're on an
   unblocked network, consider re-recording them from the live API.
3. UCB rows whose head-office town isn't in the city-centroid dict fall back
   to their **RBI regional office** city — e.g. "Banks in Jaipur" includes
   Rajasthan district UCBs (Balotra, Baran…) pinned near Jaipur. Acceptable
   for state-level prospecting; fix by extending `geo.CITY_COORDS` or
   geocoding pincodes at ingest.
4. No keys are set in this environment, so batch LLM scoring ran in
   rule-based-only mode (it logs + reports `llm: skipped`). Set
   `OPENROUTER_API_KEY` and confirm one search produces LLM-refined scores.
5. **Deferred Phase 2 items** (now listed as "Phase 2 leftovers" in the
   roadmap): auth, background search jobs with progress streaming, cache
   persistence across restarts (cache is in-process; Render free-tier sleep
   clears it).
6. `requirements-ingest.txt` (openpyxl/pdfplumber) is needed only to rebuild
   the registry or run the two registry-parser tests; they self-skip if the
   libs are missing/broken.
