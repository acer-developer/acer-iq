# ACER-IQ — Progress Checklist

**This file is the single source of truth for what is done.** Every nightly run
reads it first and updates it last. Do not start an item that is already checked.
Do not check an item that is not committed, tested and pushed.

Owner: **OP** = operator (human, cannot be automated) · **NB** = nightly build.

Last updated: 2026-09-28 · Phase in progress: **Phases 0-3 built; review fixes + operator steps** (overnight multi-session build, operator-approved 2026-09-28)

---

## Cleanup — smallest items, done FIRST

These lead deliberately. The first run should prove the whole loop works
(clone, edit, test, boot, push, tick this file) on something low-risk before it
touches a migration.

- [x] **NB** ~~Write BUILD_PLAN.md~~ (2026-09-28)
- [x] **NB** ~~Write PREMORTEM.md~~ (2026-09-28)
- [x] **NB** ~~Delete `frontend/src/components/MapView.jsx` and its use in `App.jsx` (India map dropped)~~ (2026-09-28)
- [x] **NB** ~~Delete the stale "falls back to mock data" line in `README.md` — untrue, there is no mock data~~ (2026-09-28)

## Phase 0 — Foundations *(BLOCKING — see PREMORTEM §7)*

- [x] **OP** ~~Run `supabase_schema.sql` in the Supabase SQL editor~~ (2026-09-28, project `gmqyelarfyqfsyqvrvzj`; all 4 tables present with RLS on; Render `SUPABASE_URL`/`SUPABASE_KEY` set, `/api/health` reports `Search store (supabase)`)
- [x] **OP** ~~Enable Email auth provider~~ (2026-09-28, confirm-email on)
- [x] **OP** ~~Add the Vercel app URL to auth redirect URLs~~ (2026-09-28, Site URL `https://acer-iq.vercel.app`, redirect `https://acer-iq.vercel.app/**`)
- [x] **OP** ~~Decide always-on hosting~~ (2026-09-28: **Oracle Cloud Always Free**)
- [ ] **OP** Create the Oracle Always Free VM (Ubuntu, Mumbai/Hyderabad region, ports 80/443 open) and share its public IP
- [ ] **OP** Decide: fold `acer-cra-tracker` in as the ingestion layer, or keep separate
- [x] **NB** ~~Wire the login screen and switch `saved_leads` off SQLite~~ (2026-09-28, `8b9c6ba`) — **live only once Vercel has `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY`** (see OP item below)
- [ ] **OP** Vercel → Settings → Environment Variables: add `VITE_SUPABASE_URL` = `https://gmqyelarfyqfsyqvrvzj.supabase.co` and `VITE_SUPABASE_ANON_KEY` = the anon/publishable key, then redeploy. Re-run `supabase_schema.sql` (idempotent) for the new `cin` column and tables.

## Phase 1 — Durable persistence *(REWRITTEN after the premortem)*

Not plain SQLite. `pipeline.sqlite` is gitignored and Render has no disk, so
production writes are destroyed on every restart — PREMORTEM §1.

- [x] **NB** ~~Move `action_history` onto the Postgres-with-SQLite-fallback
      pattern already in `backend/database.py`~~ (2026-09-28, `bfc18b1`). Supabase
      `cra_actions` table added to `supabase_schema.sql`; **not durable until the
      OP runs that file** — until then it falls back to SQLite and
      `stats()["durable"]` says `false`. `news_archive` does not exist yet; it is
      built on the same pattern in the next item.
- [x] **NB** ~~`news_archive` table: append-only, dedupe key, `since(days)`, `stats()`, offline `_demo()`~~ (2026-09-28, `489ec4b`; Supabase table in `supabase_schema.sql`, durable once the OP re-runs it)
- [x] **NB** ~~Wire `market_news.py` and `rss_news.py` to record on every poll~~ (2026-09-28, `489ec4b`; plus `/api/poll` + `.github/workflows/keepalive.yml` every 10 min so it fills with nobody logged in)
- [x] **NB** ~~Per-source `last_successful_read` recorded on every fetch~~ (2026-09-28, `489ec4b`; `source_health.py`, durable `source_reads`)
- [x] **NB** ~~`/api/health` reports per-source freshness, not just process liveness~~ (2026-09-28, `489ec4b`; pages on 48h silence; `health-alarm.yml` emails on degraded)
- [x] **NB** ~~Empty states say *which* — "no signal today" vs "source unreachable since X"~~ (2026-09-28, Queue `489ec4b`, Market News `14ee17b`)

## Phase 2 — Tab 3, News kept

- [x] **NB** ~~Market News reads the archive, not the live call~~ (2026-09-28, `14ee17b`)
- [x] **NB** ~~Major-only classifier (routine board approvals and market chatter are not news)~~ (2026-09-28, `14ee17b`; `news_classify.py`, rule-based)
- [x] **NB** ~~Every row renders source link + read date + one-line why-it-matters~~ (2026-09-28, `14ee17b`)

## Phase 3 — Tab 1, Macro

- [x] **NB** ~~Macro event → sector tag~~ (2026-09-28, `14ee17b` / `312ee75`; `news_classify.sectors_for` with knock-ons)
- [x] **NB** ~~Join: sector × (debt maturing <9mo OR thin interest coverage) × `winnability.py`~~ (2026-09-28, `312ee75`; `macro.py`)
- [x] **NB** ~~Output is a named list with reason + source per name. A join, not a model.~~ (2026-09-28, `312ee75`; Macro tab)

## Phase 4 — Tab 2, Monthly BD list *(needs Phase 0)*

- [ ] **NB** Deterministic, re-runnable generation
- [ ] **NB** Snapshot stored, not recomputed on view — "why was I given this name" stays answerable
- [ ] **NB** Split across 4 named BDs
- [ ] **NB** Degrades with stale inputs: still generates, names which inputs were stale
- [ ] **NB** Month-end outcomes append to `lead_events`

## Phase 5 — FreeLLMAPI

- [ ] **NB** Add as one entry in `llm.py` `_providers()` (self-hosted, OpenAI-compatible)
- [ ] **NB** Every LLM field degrades to a rule-based sentence, never to blank (PREMORTEM §6)

## Phase 6 — Tab 4, Company deep-dive *(BLOCKED on the Phase 0 tracker decision)*

- [ ] **NB** Never render a rating list as complete — state agencies searched, unreachable, and name-match risk (PREMORTEM §5)

---

## Standing rules for every run

1. One landable chunk per run. Leave the app working.
2. No dummy data. Ever.
3. Every figure carries a source + read date. Every item carries a reason.
4. Tests pass before commit. App boots and `/api/health` answers before push.
5. Blocked is a fine outcome — write why in `NIGHTLY_LOG.md` and stop. A
   plausible-looking fake is the only unacceptable one.
