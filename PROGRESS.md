# ACER-IQ — Progress Checklist

**This file is the single source of truth for what is done.** Every nightly run
reads it first and updates it last. Do not start an item that is already checked.
Do not check an item that is not committed, tested and pushed.

Owner: **OP** = operator (human, cannot be automated) · **NB** = nightly build.

Last updated: 2026-09-29 · Phase in progress: **Phases 0–4 built and pushed** — operator: re-run `supabase_schema.sql` once more; Phase 5 next; Phase 6 blocked on the tracker decision

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
- [ ] **OP** *(deferred — not needed before Phase 5)* Oracle Always Free VM. Operator found sign-up too slow (2026-09-28); Render free + `.github/workflows/keepalive.yml` (every 10 min) now keeps the backend awake and the archives filling. A real box is only needed to self-host FreeLLMAPI (Phase 5).
- [ ] **OP** Decide: fold `acer-cra-tracker` in as the ingestion layer, or keep separate
- [x] **NB** ~~Wire the login screen and switch `saved_leads` off SQLite~~ (2026-09-28, `8b9c6ba`) — **live only once Vercel has `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY`** (see OP item below)
- [x] **OP** ~~morning step 1: re-run `supabase_schema.sql`~~ (2026-09-28, done by operator; `/api/health` shows both archives durable)
- [x] **OP — re-run `supabase_schema.sql` once more** (2026-09-29, done) (idempotent): Phase 4 added `bd_lists`, `saved_leads.owner / stage_details / next_followup_date / origin`, and the trigger now records stage details and reassignments. Until then the BD list falls back to SQLite (says "Not durable yet") and stage moves in production fail with a column error.
- [x] **OP** ~~Render `SUPABASE_SERVICE_KEY`~~ (2026-09-28, done by operator)
- [ ] ~~**OP — morning step 2**~~ (done) Render → acer-iq → Environment: add `SUPABASE_SERVICE_KEY` = Supabase → Project Settings → API Keys → **service_role / secret** key. Server-only — never put it in Vercel. Without it, after step 1 the archives fall back to SQLite and `/api/health` says "not durable".
- [x] **OP** ~~Vercel `VITE_SUPABASE_*`~~ (2026-09-28, done by operator; login live)
- [ ] ~~**OP — morning step 3**~~ (done) Vercel → Settings → Environment Variables: add `VITE_SUPABASE_URL` = `https://gmqyelarfyqfsyqvrvzj.supabase.co` and `VITE_SUPABASE_ANON_KEY` = the **anon / publishable** key, then Redeploy. This turns on the login screen and per-BD pipelines.
- [x] **OP — morning step 4** (2026-09-29: operator created all four users in Supabase with auto-confirm; still turn off sign-ups) Each of the four BDs: open the app → Create account → click the email link → sign in. Then Supabase → Authentication → Sign In / Providers → turn **off** "Allow new users to sign up", so nobody else can register.
- [x] **OP — optional** (2026-09-29, `04f6f11`; ADMIN_EMAILS already developer@acerratings.com) Send the four BDs' emails (→ `backend/data/bd_roster.json`) and set `ADMIN_EMAILS` (the Head of BD's email, comma-separated) in Render → Environment. Until then every signed-in user has Admin and a reassigned lead stays editable by its previous owner (`/api/health` → `access` says which mode is on).
- [ ] **OP — check** `https://acer-iq.onrender.com/api/health` → `"status": "ok"`, `archives.*.durable: true`. The `health-alarm` GitHub workflow emails the repo owner whenever it is not.

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

Built to **`BD_LIST_SPEC.md`** (Head of BD's decisions: split, row fields,
mandatory fields per stage move, admin view, month end). Profiles: "View as"
Admin (default) / Hema / Avinash / Akash / Udit.

- [x] **NB** ~~Deterministic, re-runnable generation~~ (2026-09-29, `daaccbf`; built to `BD_LIST_SPEC.md` — the Head of BD's spec)
- [x] **NB** ~~Snapshot stored, not recomputed on view — "why was I given this name" stays answerable~~ (2026-09-29, `70dde96`/`daaccbf`; `bd_lists`, versions never overwritten)
- [x] **NB** ~~Split across 4 named BDs~~ (2026-09-29, `daaccbf`; Hema NBFC/HFC/MFI, Avinash manufacturing, Akash infra/RE/power, Udit SME/BLR; pool snake-fill; never padded)
- [x] **NB** ~~Degrades with stale inputs: still generates, names which inputs were stale~~ (2026-09-29, `daaccbf`)
- [x] **NB** ~~Month-end outcomes append to `lead_events`~~ (2026-09-29, `daaccbf`; highest stage reached + touched; rollover rules per spec)

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
5. **Operator rule (2026-09-29): every design decision goes to a "Head of BD"
   reviewer first** — spawn an agent playing ACER's Head of BD, give it the
   options, let it decide fields / what is mandatory / what Admin sees, then
   build to its answer and record it in `BD_LIST_SPEC.md`. Use gstack (plan
   review before, pre-landing review after) and `ponytail:` comments on shortcuts.
6. **Operator rule: statuses, not stages.** BDs use Pending / In progress /
   Closed (Won or Lost). Do not re-add stage forms or mandatory fields.
7. Blocked is a fine outcome — write why in `NIGHTLY_LOG.md` and stop. A
   plausible-looking fake is the only unacceptable one.
