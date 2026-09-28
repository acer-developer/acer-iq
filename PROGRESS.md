# ACER-IQ — Progress Checklist

**This file is the single source of truth for what is done.** Every nightly run
reads it first and updates it last. Do not start an item that is already checked.
Do not check an item that is not committed, tested and pushed.

Owner: **OP** = operator (human, cannot be automated) · **NB** = nightly build.

Last updated: 2026-09-28 · Phase in progress: **Phase 1 — Durable persistence**

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

- [ ] **OP** Run `supabase_schema.sql` in the Supabase SQL editor *(now also creates `cra_actions`, the CRA archive — safe to re-run, every statement is `if not exists` / `drop policy if exists`)*
- [ ] **OP** Enable Email auth provider
- [ ] **OP** Add the Vercel app URL to auth redirect URLs
- [ ] **OP** Decide always-on hosting (Oracle Always Free vs ~$7/mo)
- [ ] **OP** Decide: fold `acer-cra-tracker` in as the ingestion layer, or keep separate
- [ ] **NB** Wire the login screen and switch `saved_leads` off SQLite *(needs the 3 OP items above)*

## Phase 1 — Durable persistence *(REWRITTEN after the premortem)*

Not plain SQLite. `pipeline.sqlite` is gitignored and Render has no disk, so
production writes are destroyed on every restart — PREMORTEM §1.

- [x] **NB** ~~Move `action_history` onto the Postgres-with-SQLite-fallback
      pattern already in `backend/database.py`~~ (2026-09-28, `bfc18b1`). Supabase
      `cra_actions` table added to `supabase_schema.sql`; **not durable until the
      OP runs that file** — until then it falls back to SQLite and
      `stats()["durable"]` says `false`. `news_archive` does not exist yet; it is
      built on the same pattern in the next item.
- [ ] **NB** `news_archive` table: append-only, dedupe key, `since(days)`, `stats()`, offline `_demo()`
- [ ] **NB** Wire `market_news.py` and `rss_news.py` to record on every poll
- [ ] **NB** Per-source `last_successful_read` recorded on every fetch
- [ ] **NB** `/api/health` reports per-source freshness, not just process liveness
- [ ] **NB** Empty states say *which* — "no signal today" vs "source unreachable since X"

## Phase 2 — Tab 3, News kept

- [ ] **NB** Market News reads the archive, not the live call
- [ ] **NB** Major-only classifier (routine board approvals and market chatter are not news)
- [ ] **NB** Every row renders source link + read date + one-line why-it-matters

## Phase 3 — Tab 1, Macro

- [ ] **NB** Macro event → sector tag
- [ ] **NB** Join: sector × (debt maturing <9mo OR thin interest coverage) × `winnability.py`
- [ ] **NB** Output is a named list with reason + source per name. A join, not a model.

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
