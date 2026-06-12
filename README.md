# ACER-IQ — B2B Lead Generation for Credit Rating Agencies

Lead discovery and company research for **ACER**, a SEBI-registered Indian
credit rating agency. Two modules:

- **Find Leads** — city/pincode + entity type (Bank / NBFC / Corporate) →
  complete universe of head offices from the **committed RBI registry**
  (9,000+ NBFCs, 1,400+ cooperative & small finance banks), enriched with
  BSE debt instruments and scored (rule-based + one batched LLM call).
- **Company Research** — company name or CIN → 7-agency rating coverage
  matrix from BSE debt data + AI fit analysis.

See [ROADMAP.md](ROADMAP.md) for the plan and
[PIPELINE_RADAR_SPEC.md](PIPELINE_RADAR_SPEC.md) for the next module (on hold).

---

## Quick Start (Local)

```bash
pip install -r requirements.txt

# optional — only to rebuild the committed registry from RBI sources
pip install -r requirements-ingest.txt
python -m backend.registry.ingest

cd frontend && npm install && npm run build && cd ..
uvicorn backend.main:app --reload --port 8000
```

Open http://localhost:8000. The registry-backed search (Banks / NBFCs) works
**with zero API keys and zero network access** — `backend/registry/data/registry.sqlite`
is committed.

Dev mode: run `npm run dev` in `frontend/` (port 5173, proxies to the backend).

## Environment variables (`.env`, all optional)

| Key | Used for | Free tier |
|-----|----------|-----------|
| `OPENROUTER_API_KEY` | AI lead scoring + fit analysis (free models) | yes — [openrouter.ai](https://openrouter.ai) |
| `GOOGLE_PLACES_API_KEY` | Corporate discovery fallback, office locations | $200/mo credit |
| `HUNTER_API_KEY` | Email contact enrichment | 25 searches/mo |
| `SUPABASE_URL` / `SUPABASE_KEY` | Search history persistence | yes |
| `CORS_ORIGINS` | Comma-separated allowed origins (default `*`) | — |

## Tests

```bash
pip install pytest
pytest backend/tests -q
```

Parser tests run against recorded fixtures (`backend/tests/fixtures/`) and the
committed RBI source files — no network needed.

## Architecture

```
Browser → React (Vite + Leaflet) → FastAPI
    discovery:  RBI registry (SQLite, committed)   ← Banks & NBFCs, instant
                OSM Overpass / Google Places       ← Corporates fallback
    enrichment: BSE debt + CorpInfo APIs           ← instruments, CIN, directors
                Zauba Corp                         ← unlisted companies / CIN lookups
                Hunter.io                          ← email contacts
    scoring:    rule-based + OpenRouter LLM        ← ONE batched call per search
    plumbing:   backend/netutil.py                 ← TTL cache, per-source rate
                                                     limits, per-source status
```

Every `/api/search` and `/api/company-credit` response includes a `sources`
map showing which external sources succeeded / failed / were skipped.

## Folder Structure

```
backend/
├── main.py              # FastAPI app, routes, per-request source status
├── netutil.py           # TTL cache, per-source semaphores, SourceStatus
├── registry/
│   ├── ingest.py        # RBI XLSX/PDF → registry.sqlite (committed)
│   ├── store.py         # read-side queries powering discovery
│   ├── geo.py           # CIN/pincode → state, city centroids
│   └── data/            # RBI source files + registry.sqlite
├── pipeline/            # discovery, BSE/Zauba scrapers, scoring, fit analysis
└── tests/               # pytest parser tests + recorded fixtures
frontend/                # React + Vite + Tailwind + Leaflet
```

## Deploy

`render.yaml` / `railway.toml` / `Procfile` are set up — push to GitHub,
connect the repo, add env vars. The registry ships in the repo, so no DB
setup is needed.
