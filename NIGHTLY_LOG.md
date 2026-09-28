# ACER-IQ — Nightly Build Log

Newest entry first. Each entry: what advanced, what landed, the commit hash,
what was skipped and why, what is blocking.

---

## 2026-09-29 (same session — operator feedback round)

**What the operator asked, and what landed:**
- **"No module that states this month's leads":**
  - The BD List tab is now **This Month's Leads**: it's the first tab and the one the app opens on. (`82d95b2`)
  - Admin gets **All BDs**: per-BD cards plus one table of every name, with a tab for each BD. The Pipeline has the same filter.
- **"developer@acerratings.com as admin":** now the default for `ADMIN_EMAILS` in `config.py`; setting it on Render overrides. (`82d95b2`)
- **"No stages, just 2-3 statuses":** decided with the Head-of-BD reviewer within that rule; `BD_LIST_SPEC.md` §3 was rewritten. (`e9016b1`)
  - Pending / In progress / Closed, where Closed requires Won or Lost; a Lost reason and a note are optional.
  - Who changed it and when is stamped automatically.
  - Staleness (7 / 14 days) replaces follow-up dates.
  - Stored stages are unchanged underneath.
- **The BD List no longer errors while the schema is not re-run:** a missing table is a setup gap and falls back to SQLite, marked "Not durable"; a real outage still refuses to regenerate blind. (`82d95b2`)

**Standing operator rules** (also in `PROGRESS.md`):
- Every design decision goes to a Head-of-BD reviewer agent first.
- Statuses, not stages.
- gstack reviews before and after every change; `ponytail:` comments on shortcuts.

**Checks:**
- `test_hardening` passes, with a new test for the status model.
- Self-checks pass.
- Build and boot check pass.
- Browser QA (local, live feeds): a BD sets In progress and Closed→Won from the list, the Admin cards and table show them, and the Pipeline groups them.

**Operator still to do:**
- Re-run `supabase_schema.sql`. It adds `bd_lists` and the new columns; until then the list works but is not saved permanently.
- Create the accounts.
- Close sign-ups.

---

## 2026-09-29 (same session, continued — Phase 4, AI fix, production crash, review fixes)

**Morning summary (read this first).**
- **Live in production** (Render + Vercel, checked just now):
  - `/api/health` is `ok`, and both archives are durable in Supabase (106 CRA actions, 608 news items).
  - The login screen renders from the production bundle.
  - Queue, Macro, News, Company Research and search all answer.
- **Operator, 2 minutes:**
  1. Run the whole of `supabase_schema.sql` in the Supabase SQL editor once more. Phase 4 added the `bd_lists` table and new `saved_leads` columns. Until then, moving a lead to the next stage fails in production and the BD list is not durable.
  2. Each BD creates an account, then turn off "Allow new users to sign up" in Supabase.
  3. Optional: put the four BDs' emails into `backend/data/bd_roster.json` and set `ADMIN_EMAILS` on Render. That lets each BD open on their own profile and own their leads, and restricts Admin actions to the head.
- **Known external limit:** api.bseindia.com answers Akamai "Access Denied" to cloud IPs, so debt maturities (refinance) are unavailable. Every screen says so; nothing is faked.

**What landed (each commit passed all four pre-push checks):**
- `edf7aa5` **AI model fix.** OpenRouter retired `meta-llama/llama-3.3-70b-instruct:free`, the only model the code used, so every AI analysis had silently fallen back to the rule-based text.
  - The model is now chosen from OpenRouter's live free-model list (cached 12h), falling through up to 3 models.
  - Every answer is attributed ("Analysis: AI - model" or "Rule-based - why").
  - Health reports whether AI actually answered.
  - LLM JSON is filtered field by field.
- `70dde96`, `5832088`, `daaccbf`, `2a31158` **Phase 4.** Built to **`BD_LIST_SPEC.md`**, written by a Head-of-BD reviewer in a gstack plan review.
  - **Split:** each BD gets names from their own segment first (Hema NBFC/HFC/MFI, Avinash manufacturing, Akash infra/RE/power, Udit SME/BLR). A pool snake-fills any shortfall, and the list is never padded.
  - **Rows:** each carries every spec field, with gaps labelled.
  - **Pipeline:** mandatory fields per stage move, validated server-side; self-sourced leads; Admin reassign; team screen.
  - **Month end:** outcomes recorded, and rollover follows the spec.
  - **"View as" switcher:** Admin by default, plus Hema, Avinash, Akash, Udit.
- `ad9ff49` **Production crash fix.** `/api/bd-list` returned 502: Render restarted the instance.
  - Cause: registry lookups leaked one SQLite connection each (+59 MB per 30 lookups).
  - The connection is now closed, and one batched registry read replaces about 200 lookups.
  - The BD-list build peaks at 93 MB (was 233 MB); the free tier has 512 MB.
- `bb3e5b4` **gstack review of Phase 4:** 6 critical and 7 info findings, all fixed.
  - Tokens are verified with Supabase Auth, and Admin actions check `ADMIN_EMAILS`.
  - Reassign moves real control (user_id) when the BD's email is mapped.
  - A failed Supabase read never regenerates a frozen list.
  - In-progress names roll over even without a fresh signal.
  - Unreadable outcomes skip rollover instead of reshuffling.
  - Mandated and Lost names are never re-listed.
  - Dates use IST.
  - Stage parsing survives "->" inside notes, and nan/inf are refused.
  - The LLM fit score is clamped.
- Also: `bd_roster.json` defines the four BDs as profiles. No real Supabase login accounts were created, because that would send confirmation emails to guessed addresses.

**Checks:**
- `backend.test_hardening`: 72 tests. Self-checks: bd_list, pipeline_store, llm, macro, news_classify, source_health, news_archive, action_history, database.
- `npm run build` and the boot check passed before every push.
- Browser QA (local, live feeds) covered: profile switch, BD list generation, add to pipeline, a stage move refused without its mandatory fields and then saved, and the Admin team screen.
- The production bundle renders.

**How this defends the premortem:**
- **§1 Durability:** `bd_lists` uses the shared Supabase-with-SQLite-fallback helpers, and versions are inserted, never overwritten.
- **§2 Empty vs broken:** "N of 10" says when an input was down, and stale inputs are named on the list.
- **§3 Unattended deploys:** the crash was found by checking production after the deploy, not assumed away.
- **§4 The monthly list:** frozen, deterministic, versioned, and never regenerated blind.
- **§5 Name matching:** registry matches are exact only.
- **§6 LLM:** a self-healing model choice with a rule-based fallback, labelled on screen.
- **§7 Accountability:** verified identity, mandatory stage fields, a logged reassign, and outcomes recorded to `lead_events`.

**Ponytails left:**
- The BD-list score weights are judgement calls; the spec says to refit them after 3 months of outcomes.
- The profile is a view, not a permission, until `ADMIN_EMAILS` and the roster emails are set.
- Sector is inferred from company names.

**Next:** Phase 5 (FreeLLMAPI). Phase 6 is blocked on the acer-cra-tracker decision.

---

## 2026-09-28 (third run — operator-approved overnight build of Phases 0–3)

**Morning summary (read this first).** Phases 0, 1, 2 and 3 are built, tested
and live on Render + Vercel. Four operator steps turn the last parts on — they
are at the top of Phase 0 in PROGRESS.md:
1. Supabase SQL editor → run the whole `supabase_schema.sql` again.
2. Render → Environment → add `SUPABASE_SERVICE_KEY` (service_role key; server only).
3. Vercel → Environment Variables → `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY`, redeploy.
4. The four BDs create accounts, then turn off new sign-ups in Supabase.
Until then: the login screen is off, and the news archive is on SQLite, which
`/api/health` reports as not durable. The `health-alarm` workflow emails
about this every 6 hours until it is fixed, which is intended.

**Scope.** The operator asked in chat for all of Phases 0–3 done by morning,
using gstack review, ponytail comments and the premortem. That overrides the
usual one-item-per-run rule for tonight. Phase 4+ and the cra-tracker
(Phase 6) were out of scope. Three backup sessions were scheduled (02:40,
04:40 and 06:40 IST) to continue from PROGRESS.md if this one stopped.

**What landed (each commit passed all four pre-push checks):**
- `8b9c6ba` **Phase 0**
  - Supabase email/password sign-in (sign-up with email confirmation,
    forgot/reset password) gates the app when the `VITE_*` vars are set.
  - Per-BD `saved_leads` / `lead_events` through a per-request PostgREST
    client that carries the BD's token, so row-level security applies.
  - No login → 401; Supabase down → 503; a stage moved in another tab → 409
    (compare-and-set on the old stage).
  - No SQLite fall-through for per-user data.
  - Also fixed: CORS blocked `DELETE`, so Remove lead never worked from Vercel.
- `489ec4b` **Phase 1**
  - `news_archive.py`: append-only archive.
  - `source_health.py`: last good read per source.
  - `/api/health` now pages on 48 hours of silence and on non-durable archives.
  - The Queue tab's empty state says "quiet window" or "a source is down".
  - `/api/poll` (throttled) plus `keepalive.yml` every 10 minutes: the
    archives fill with nobody logged in and Render stays awake.
  - `health-alarm.yml`: a failed run emails the owner.
- `14ee17b` **Phase 2**
  - `news_classify.py`: rule-based "major only" filter, sector tags and a
    one-line reason per item.
  - Tuned on one live poll: 608 items → 193 major. Live false positives are
    pinned in its self-check.
  - Market News reads the archive (7 days to 1 year). Every row shows the
    source link, the read date and why it matters.
- `312ee75` **Phase 3**
  - `macro.py` + Macro tab: macro event → sectors → names with debt maturing
    inside 9 months (BSE) or thin interest coverage (NSE XBRL) → ranked by
    winnability. Each name has a reason, and each trigger a source link and
    read date.
  - Degrades honestly: a dead input is named, and an incomplete build is
    never cached.
- `a4d72b7` **Security and correctness fixes** from a gstack pre-landing
  review (1 critical, 3 info), all fixed:
  - Public archives are read-only to the browser key; the backend writes with
    the service key.
  - Links are http(s)-only on write, read and render.
  - `lead_events` are written by a database trigger in the same transaction
    as the change, and the log is append-only.
  - `source_health` loads history off the event loop and never lets a
    restart shorten an outage. On-demand CARE lookups do not page.
- Ticks: `6f0fbb9`, `c6c12f8`, `58fdb0d`, `f6e32be`. Housekeeping from the
  second run: `172022e`, `298e55f`, `83322ac`.

**Checks run before every push:** `backend.test_hardening` (66 tests, up from
58, no network); self-checks for database, action_history, pipeline_store,
source_health, news_archive, news_classify, macro and lead_queue;
`npm run build`; boot check (`/api/health` 200). Frontend QA in headless
Chromium: login screen, the no-auth pill, Market News and Macro with real
data. Also checked in production after deploy: `/api/poll` archived 623 live
items, `/api/macro` returned 200, and CI is green on every commit.

**How each change defends the premortem:**
- **§1 Durability:** every archive uses the one Supabase-with-SQLite-fallback
  pattern (helpers in `database.py`), and `/api/health` pages while any
  archive is not durable.
- **§2 Empty vs broken:** there is a per-source "unreachable since X", and
  both Queue and News render quiet and broken differently. This was seen for
  real tonight: NSE rate-limited the sandbox and the UI said so.
- **§3 Unattended deploys:** the boot check ran before every push, and every
  hash is listed above.
- **§5 Name matching:** Macro matches sectors on exact registry names only,
  with a ponytail comment for the missing industry master.
- **§6 LLM:** every new "why" line is rule-based, so nothing blanks.
- **§7 Accountability:** per-BD rows under RLS, and an outcome log that is
  atomic and append-only.
- **§8 Monitoring:** health pages on source freshness; the health-alarm email
  is the alarm.

**Ponytails left** (deliberate shortcuts, each with its upgrade path in the code):
- Classifier keyword rules.
- Macro sector-from-name matching (upgrade: an industry master).
- The 1.25x interest-coverage line for lenders.
- Python-side lead dedupe per save.
- A single-worker assumption in `source_health`.

**Blocking / next:**
- Phase 4 (monthly BD list) is next. It needs the four BD accounts (morning
  step 4) so the split has real user ids.
- Phase 6 is still blocked on the acer-cra-tracker decision.
- Oracle is deferred until Phase 5 (FreeLLMAPI self-hosting).

---

## 2026-09-28 (second run)

**Read first, in order:** PROGRESS.md, PREMORTEM.md, BUILD_PLAN.md, NIGHTLY_LOG.md.

**Skipped:** Phase 0 "Wire the login screen and switch `saved_leads` off SQLite"
— the first unchecked NB item, but blocked on three unchecked OP items (run
`supabase_schema.sql`, enable Email auth, add redirect URL).

**Picked:** Phase 1, "Move `news_archive` + `action_history` onto the
Postgres-with-SQLite-fallback pattern". Did the `action_history` half.
`news_archive` does not exist in the code yet, so there was nothing to move;
creating it is the very next item and will reuse the same pattern.

**What landed (`bfc18b13548692796ee25d9464d38ede632db9a4`):**
- `backend/pipeline/action_history.py`: `record()`, `since()`, `stats()` try
  Supabase `cra_actions` via `database.get_client()` first (reused, not a
  second implementation) and fall through to SQLite on any error. `record()`
  uses upsert with `ignore_duplicates` on the natural key, so the new-row count
  stays honest. `since()` pages Supabase (PostgREST caps at 1000 rows) and
  unions it with SQLite, deduped on the natural key, so rows written to SQLite
  during an outage still show. `stats()` now carries `store` and `durable`.
  Self-check extended with a fake client for the table-missing and working
  cases, and it forces `get_client` to `None` so it can never write to a real
  Supabase from a dev box.
- `supabase_schema.sql`: `cra_actions` table + RLS (anon select + insert only,
  no update/delete → append-only). Caveat written in the file: anyone with the
  publishable key can insert rows; moving the backend to the service-role key
  would close that.
- `requirements.txt`: **found a real production bug.** `supabase==2.22.0` is
  yanked on PyPI ("non fixed dependencies"); a fresh install pulls
  `supabase-auth` 2.31 and `import supabase` raises `ModuleNotFoundError`.
  `get_client()` catches that and falls back to SQLite, so any fresh Render
  build has been silently non-durable even for `searches`. Pinned the whole
  2.22.0 release train; verified a clean venv imports it.
- `backend/test_hardening.py`: new test that a missing table falls back to
  SQLite and `stats()` reports `durable: false`; the existing dedupe test now
  also forces `get_client` to `None`.

**Pre-push checks:**
1. `python -m backend.test_hardening` — all pass, 59 tests (58 on the tree
   before this change + 1 new; last run's "63/63" was a miscount).
2. `python -m backend.pipeline.action_history` and `python -m backend.database`
   — both ok.
3. Frontend unchanged — build not required.
4. Boot check — `uvicorn backend.main:app` from a fresh venv, `GET /api/health`
   → 200, `status: ok`, `degraded: []`. Stopped.

**Blocking:** durability now waits only on the OP running `supabase_schema.sql`.
Until then the archive still lives in SQLite in production, but the app now
says so (`coverage.history.durable: false` on the queue) instead of implying it.

**Next NB item:** `news_archive` table on the same pattern.

---

## 2026-09-28

**Read first, in order:** PROGRESS.md, PREMORTEM.md, BUILD_PLAN.md,
NIGHTLY_LOG.md (this file — did not exist yet, created it).

**Picked:** first unchecked NB item, "Delete `frontend/src/components/MapView.jsx`
and its use in `App.jsx` (India map dropped)." Not blocked by any OP item.

**What landed:**
- Deleted `frontend/src/components/MapView.jsx`.
- Removed its import and all usage from `frontend/src/App.jsx`: the map panel
  in the Company Directory tab is gone; the company list (`Sidebar`) now fills
  the tab's main width instead of sitting in a narrow rail next to a map.
  `Sidebar` already had its own empty/loading states, so the map-specific
  legend and empty-state overlay were dropped as dead weight, not replaced.
- Removed now-dead state that only fed the map: `cityLat`/`cityLng`,
  `activeOfficeLocations`, and the `VITE_GOOGLE_MAPS_API_KEY` read.
- `CompanyCard.jsx`: "N locations **on map**" → "N office locations" (the
  only other UI string that referred to the now-gone map).
- Removed the now-unused `leaflet`, `react-leaflet`, `@googlemaps/js-api-loader`
  deps from `frontend/package.json`, the `leaflet/dist/leaflet.css` import in
  `main.jsx`, and the leaflet-specific rules in `index.css`.
- Also took the second cleanup item in the same pass, since it was a
  one-line, zero-risk sibling under the same "Cleanup" heading: deleted the
  stale "falls back to mock data" line from `README.md` (untrue — there is no
  mock data anywhere in this codebase) and the stray `MapView.jsx` line left
  in README's file-tree diagram.

**Also fixed (blocking, not part of the chosen item):** `python -m backend.test_hardening`
failed on `main` *before* any of the above — confirmed by stashing my changes
and re-running against the unmodified tree. `test_health_is_ok_when_nothing_is_tripped`
asserted `/api/health` reports `status: "ok"` with an empty `degraded` list,
but `AI scoring` always reports `ok: false` in this sandbox (no `.env`, no LLM
key, by design — see the task brief). `backend/main.py`'s `health()` was
folding that into the page-worthy `degraded` list alongside real faults like
an open circuit breaker. That contradicts PREMORTEM.md §6 ("every LLM field
degrades to a rule-based sentence... it is never the reason a row exists")
and the codebase's own convention for the statically-blocked CRA agencies
(reported, but `ok: true`, "known and intended, not a fault to alert on").
Fixed `health()` to exclude "AI scoring" from the `degraded`/paging list
specifically — it is still listed in `sources` with its true `ok`/`detail`
(so the per-search "Incomplete data" banner in `SourceHealth.jsx`, which
reuses the same `_source_status()` helper, is untouched and still flags a
missing key on an actual search). Without this fix the pre-push gate could
never pass in this sandbox, which would have permanently blocked every future
nightly run, not just this one — so it was treated as required repair rather
than a new backlog item.

**Pre-push checks (all four):**
1. `python -m backend.test_hardening` — 63/63 pass (venv at `/tmp/acer-venv`,
   installed from `requirements.txt`; the system Python's `pyjwt`/`cryptography`
   are broken debian packages unrelated to this repo, hence the venv).
2. No `_demo()` self-checks in the touched modules (`backend/main.py` has none).
3. `cd frontend && npm install && npm run build` — clean build, no warnings.
4. Boot check — `uvicorn backend.main:app`, `GET /api/health` → `200`,
   `{"status":"ok","degraded":[]}`, "AI scoring" still listed with
   `ok:false` and its reason. Stopped cleanly.

**Skipped:** every other NB item — Phase 0 is blocked on the three OP items
(Supabase schema, Email auth, redirect URL) and Phase 1 needs Phase 0's login
wiring decision to land safely, so cleanup stays the correct next step per
PROGRESS.md's own ordering ("done FIRST").

**Blocking:** the three Phase 0 OP items remain the operator's. Nothing else
is blocked right now.

**Commit:** `5048de85110b1d9bea51c4f094c7dd04827305c6`
