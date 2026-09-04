# ACER-IQ — TODO

Live status as of **2026-09-04**. Ordered by what unblocks the most value.
Rationale for the ordering is in [ROADMAP.md](ROADMAP.md); the visual flow with
every data source marked FREE or PAID is [ROADMAP_FLOW.html](ROADMAP_FLOW.html).

Legend: **FREE** = no new spend · **PAID** = costs money · **DECISION** = needs your call.

---

## Now — finish Phase 1 (complete data universe)

- [ ] **Ingest the MCA Company Master** — **FREE** — the single biggest jump in
      product value left.
      Source: <https://www.data.gov.in/catalog/company-master-data>, CSV/ZIP per
      Registrar of Companies. Carries CIN, name, status, class, authorized and
      paid-up capital, registration date, state, RoC, principal business
      activity, registered office address.
      Replaces the OpenStreetMap guesswork that currently serves the
      **Corporates** segment in `backend/pipeline/discovery.py`.
      - [ ] Add an ingest command alongside `backend/registry/ingest.py`
      - [ ] Geocode registered addresses once at ingest, not per search
      - [ ] Point the Corporate branch of `discovery.py` at the registry
      - [ ] Keep OSM as a fallback only, and label the source in the response
- [ ] **Automate the monthly registry refresh** — **FREE** — currently a manual
      CLI run, so the registry silently ages.

## Next — finish Phase 2 (make the data trustworthy end to end)

- [ ] **Persist every search to Supabase** — **FREE** (free tier is enough).
      `_search_cache` is still in-process, so a restart loses it and
      `/api/export/{id}` 404s. The code path exists and now logs loudly; it just
      needs `SUPABASE_URL` / `SUPABASE_KEY` set and the `searches` table created.
- [ ] **TTL cache for geocode and MCA lookups** — **FREE** — the same company is
      re-fetched on every search.
- [ ] **Auth gate** — **FREE** — **DECISION**: a shared-token check breaks the
      Vercel frontend until the token is wired into it, so backend and frontend
      must ship together. Right now anyone with the URL can burn our quotas.
      Say the word and I'll do both halves in one change.
- [ ] **Move to an always-on backend** — **PAID, ~$7/mo** — the only unavoidable
      recurring cost. The free Render dyno sleeps, which is the root cause of
      lost cached searches and export 404s.

## Then — Phase 3, Pipeline Radar (the flagship)

Full spec: [PIPELINE_RADAR_SPEC.md](PIPELINE_RADAR_SPEC.md). All **FREE**, but
real scraping work.

- [ ] **Per-CRA press-release scrapers** for the 7 SEBI agencies — rating
      withdrawals, issuer-not-cooperating flags, downgrades. These are the
      events that create mandates.
- [ ] **Surveillance-date extractor** from rating rationale PDFs (each agency
      formats differently; likely needs LLM extraction — now covered by the
      TokenRouter fallback).
- [ ] **Maturing-NCD refinance window** detector from the BSE scrip master we
      already cache.
- [ ] **Add ACER as the 8th agency** in Company Research using internal data —
      prevents pitching our own clients and surfaces renewals.
      **Blocked: ACER must provide the rating book. No public source has it.**

## Later — Phase 4, workflow

- [ ] **My Pipeline tab** — lead lifecycle (Identified → Contacted → Meeting →
      Proposal → Mandated / Lost), owner, notes, follow-up dates.
- [ ] **De-duplicate leads across searches**, keyed on CIN.
- [ ] **Weekly digest email.**
- [ ] **Pitch-brief PDF export** — one page per company for meetings.

## Known gaps we are accepting for now

- [ ] **Decision-maker contacts** — **PAID, no free option.** Hunter's free tier
      is 25 lookups per *month* (now capped at 5/search to protect it) and no
      free source covers Indian mid-cap CFOs. RBI-filed emails are used where
      they exist. Needs a budget decision, not code.
- [ ] **Rating fields** are absent from BSE's scrip master, so the 7-agency
      matrix rests on NSE disclosures alone. The CRA scrapers above are the fix.
- [ ] **Issue date and issue size** are unavailable from the master list; coupon
      and maturity are parsed from BSE's scrip-id convention and return blanks
      rather than guessing.
- [ ] **`_guess_entity_type`** is a name-only heuristic. Deliberately left: it
      runs in one place, only when the registry has no answer, and only sets a
      display label. The CIN's NIC code would be better but the code ranges need
      verifying first.
- [ ] **Live CIN → name resolution** — **PAID, skippable.** Probe42 / Tofler.
      Only needed for real-time company status; discovery does not need it.

---

## Done (2026-09-04)

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
      Capital: incorporated 1991, listed 2025 — the old code said 2025).
- [x] **Unresolvable CIN** 404s with a reason instead of reporting "rated by
      nobody".
- [x] **Issuer matching is exact-only** — removed prefix matching that gave a
      parent's debt and CIN to subsidiaries ("REC Power Development" inheriting
      REC Ltd's bonds). Registry `(Formerly known as …)` noise is stripped
      instead, which is what makes the exact match land.
- [x] **Hunter quota protected** — capped at 5 lookups per search.
- [x] **CORS** env-driven; dead config keys removed with `extra = "ignore"` so
      leftover Render env vars cannot stop the app booting.
- [x] **Tests + CI** — 14 checks, no network, wired to GitHub Actions with a
      frontend build and an app-import smoke test.
