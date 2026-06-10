# Pipeline Radar — Module Spec

> Third tab in the web app: **"Pipeline Radar"** — companies expected to raise
> money in the coming month/quarters, caught BEFORE they mandate a rating agency.
> This is the module a CRA CEO would check every Monday morning.
> Created 2026-06-10. Status: SPEC APPROVED FOR BUILD (pending sequencing call).

---

## 1. The CEO's mental model (what this module answers)

1. **"Who is going to need a rating, and when?"** → time-bucketed lead feed.
2. **"Which sectors should my team hunt in this quarter, and why?"** → sector
   outlook cards with cited sources.
3. **"Tell me everything about this one company before I call them."** → full
   company drill-down: management, financial snapshot, existing ratings, debt
   maturities, every signal with evidence links, recommended pitch.

Key principle: **every lead must carry a "why now" reason + a clickable source.**
No naked scores. If we can't show evidence, we don't show the lead.

---

## 2. Time horizon system (Indian FY, auto-rolling)

India fiscal year = April–March. FY27 = Apr 2026 – Mar 2027.

- All signals are stored with **absolute date windows** (`expected_from`,
  `expected_to`), never labels.
- Labels (FY-quarter chips) are **computed at request time** from today's date,
  so buckets roll forward automatically — a lead in "coming quarter" today
  becomes "this month" as time passes, with zero data migration.

Horizon chips (computed for today = 10 Jun 2026):

| Chip | Window |
|------|--------|
| This month | Jun 2026 |
| Rest of FY27 Q1 | until 30 Jun 2026 |
| FY27 Q2 | Jul–Sep 2026 |
| FY27 Q3 | Oct–Dec 2026 |
| FY27 Q4 | Jan–Mar 2027 |
| FY28 H1+ | Apr 2027 onwards |

Signals whose window has fully passed → auto-archived (status `expired`),
visible in a history view for hit-rate review ("did they actually raise?").

`fy_utils.py`: `to_fy_quarter(date) -> "FY27 Q2"`, `horizon_buckets(today) -> [...]`.

---

## 3. Signals engine (where leads come from)

| # | Signal | Expected window rule | Confidence | Source |
|---|--------|---------------------|------------|--------|
| S1 | Board meeting intimation: "to consider fund raising / NCD / QIP / rights issue" | meeting date + 0–2 months | High | BSE/NSE corporate announcements (structured API) |
| S2 | Outcome/special resolution: borrowing limit increase Sec 180(1)(c), NCD private-placement approval | approval date + 0–12 months (front-loaded) | High | BSE/NSE outcomes |
| S3 | Maturing NCDs/bonds | maturity date − 6 to − 1 months (refinance window) | High (date is certain) | BSE debt data we already fetch (`maturity_date`) |
| S4 | Shelf prospectus / DRHP filed | filing + 1–2 quarters | High | SEBI filings page |
| S5 | Expansion news: capex, new plant, acquisition, large order win, PLI winner, infra/renewable project award | article date + 2–4 quarters | Medium | Google News RSS / sector feeds → LLM classification |
| S6 | New NBFC licence granted | grant + 1–2 quarters (first-time rating) | Medium | RBI press releases |
| S7 | Competitor rating withdrawn / INC | immediate | High | Agency press-release pages |

Build order within signals: **S1+S2 first** (one structured source, highest value),
then S3 (data we already have), then S5 (news + LLM), then S4/S6/S7.

**Pipeline per signal:** fetch → dedupe (hash of source doc) → LLM classify+extract
(company, instrument, amount if stated, window, one-line reason) → attach
`source_url` + verbatim evidence snippet → upsert into `signals`.

LLM never invents the window — rules above set it; LLM only classifies type and
extracts fields. Anything the LLM can't ground in the source text gets dropped.

---

## 4. Sector outlook (the "why bullish" layer)

Weekly background job:

1. Aggregate live signals by sector → counts, total announced amounts.
2. Pull sector headlines (news RSS) for context.
3. LLM writes per-sector outlook: stance (Bullish / Neutral / Cautious),
   3–4 driver bullets, **each driver linked to a source URL**, expected
   instrument mix, top upcoming issuers.
4. Stored in `sector_outlook` with `generated_at`; UI shows freshness stamp.

Sectors (v1): NBFC/Financial services · Renewables/Power · Infra/Roads ·
Real estate/Housing finance · Steel/Cement · Pharma/Healthcare · Auto/EV ·
Agri/FMCG · Others.

Card shows: stance badge, expected-issuer count for the selected horizon,
top 2 drivers, "view N leads →".

---

## 5. UI structure (new tab: Pipeline Radar)

```
┌──────────────────────────────────────────────────────────────┐
│ [This month] [FY27 Q2] [FY27 Q3] [FY27 Q4] [FY28 H1+]        │  ← horizon chips (auto-computed)
├──────────────────────────────────────────────────────────────┤
│ SECTOR OUTLOOK (horizontal scroll cards)                     │
│ [NBFC ▲Bullish · 14 leads] [Renewables ▲ · 9] [Infra ▲ · 7]  │
├──────────────────────────────────────────────────────────────┤
│ Filters: sector ▾  state ▾  entity ▾  signal type ▾  conf ▾  │
├──────────────────────────────────────────────────────────────┤
│ LEAD FEED (cards, newest/strongest first)                    │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ Shriram Housing Finance · NBFC · Mumbai      conf: HIGH  │ │
│ │ ⚡ Board meets 18 Jun to consider NCD issue up to ₹500cr  │ │
│ │ Expected: FY27 Q2 · Instrument: NCD                      │ │
│ │ [source: BSE announcement ↗]      [Open full profile →]  │ │
│ └──────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

**Company drill-down** (click a lead → full page/drawer, reuses + extends the
existing Company Research pipeline):
- Header: name, CIN, entity type, sector, HQ, website, incorporation date
- **Management**: directors + designations + DIN (BSE CorpInfo / MCA), company
  secretary, LinkedIn search links
- **Signal timeline**: every signal we've caught for this company, dated, each
  with evidence snippet + source link
- **Existing ratings**: 7-agency matrix (reuse credit_history)
- **Debt maturity ladder**: instruments from BSE with maturity dates plotted
- **Fit & pitch**: which instrument to pitch, urgency, talk track (reuse
  fit_analyzer, now fed with the live signals)
- Actions: export one-pager, mark contacted (Phase 4 workflow hook)

---

## 6. Data model (Supabase/Postgres)

```sql
companies        -- Phase 1 table (registry-backed), referenced by CIN/id

signals (
  id uuid pk,
  company_id fk → companies (nullable until matched),
  company_name text,            -- raw, for unmatched
  signal_type text,             -- S1..S7 enum
  evidence text,                -- verbatim snippet from source
  source_url text not null,
  source_doc_hash text unique,  -- dedupe
  predicted_instrument text,    -- NCD / Bond / CP / IPO / BLR / QIP
  amount_crores numeric null,
  expected_from date, expected_to date,
  confidence text,              -- High / Medium
  sector text, state text, entity_type text,
  status text default 'active', -- active / expired / converted / dismissed
  detected_at timestamptz default now()
)

sector_outlook (
  sector text, stance text, drivers jsonb,   -- [{text, source_url}]
  instrument_mix jsonb, lead_count int,
  window_from date, window_to date,
  generated_at timestamptz
)
```

## 7. API surface

| Endpoint | Returns |
|----------|---------|
| `GET /api/radar/horizons` | computed FY-quarter buckets for today |
| `GET /api/radar/sectors?from=&to=` | sector outlook cards for the window |
| `GET /api/radar/leads?from=&to=&sector=&state=&entity=&signal=` | lead feed |
| `GET /api/radar/company/{cin_or_id}` | full drill-down profile |
| `POST /api/radar/refresh` (admin/cron) | run signal ingestion now |

Ingestion runs as scheduled jobs (daily signals, weekly sector outlook) — cron
on the host or Supabase scheduled functions — never inside a user request.

---

## 8. Build plan (steps, in order)

| Step | What | Depends on |
|------|------|-----------|
| 0 | `fy_utils` (FY/quarter math) + `signals`/`sector_outlook` tables + logging baseline | Supabase project |
| 1 | BSE corporate-announcements ingester + LLM classifier → S1/S2 signals | 0 |
| 2 | S3 maturity-ladder signals from existing BSE debt fetcher | 0 |
| 3 | Radar API endpoints + frontend tab: horizon chips, filters, lead feed | 1, 2 |
| 4 | Sector outlook job + cards | 3 |
| 5 | Company drill-down page (reuse research pipeline + signal timeline) | 3 |
| 6 | S5 news/capex signals (Google News RSS + LLM) | 4 |
| 7 | S4 SEBI filings, S6 RBI licences, S7 competitor withdrawals/INC | 6 |

Definition of done for v1 = steps 0–5: a working tab where you pick "FY27 Q2",
see sector cards, see evidence-backed leads from real BSE announcements and
maturity data, and click into a full company profile.

---

## 9. Honest constraints (so we plan around them)

- **Listed companies first.** S1–S4 cover BSE/NSE-listed issuers. Unlisted
  companies need MCA MGT-14/charge filings (paid/awkward API) — phase it later.
- **NSE announcement API is more aggressive about blocking than BSE** — start
  with BSE, add NSE with careful headers/caching.
- **News signals (S5) are noisy** — they launch *after* the structured signals
  prove the UX, and always show as "Medium confidence".
- **Free OpenRouter LLM is rate-limited** — classification must be batched and
  cached by document hash; one document is never classified twice.
