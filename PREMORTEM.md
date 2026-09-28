# ACER-IQ — Premortem

Written 2026-09-28, before Phase 1. The exercise: it is twelve months from now,
the four BDs have stopped opening the tool. Why?

Each finding below is grounded in code or config in this repo, not speculation.
Ordered by how fatal.

---

## 1. FATAL — the archive does not survive production. It does not exist there.

`backend/registry/data/pipeline.sqlite` holds the CRA action history
(`action_history.py`), the pipeline store (`pipeline_store.py`) and is where
Phase 1 was about to put the news archive.

- It is **gitignored** (`.gitignore:43`), so it is never deployed.
- `render.yaml` declares **no disk**, and runs `plan: free` — Render's free tier
  filesystem is ephemeral and the dyno sleeps and restarts.

So in production that file is created empty on boot, written to, and destroyed on
the next restart. **The 82 archived rating actions exist only on the developer's
laptop.** "News stored forever" would have been a lie the moment it shipped, and
a silent one — the app would show an empty archive and look merely new.

By contrast `registry.sqlite` *is* committed, which is why registry lookups work
in production and masked this. That is read-only reference data; it is not the
same thing.

**Required before Phase 1 writes a single row:** durable storage. Supabase
Postgres already exists and `backend/database.py` already implements the
fall-through pattern (Postgres when reachable, SQLite otherwise) — reuse it,
do not invent a second one. SQLite stays as the local-dev path only.

**This reorders the plan.** Phase 1 is not "add a table", it is "add a durable
table". See BUILD_PLAN.md.

---

## 2. FATAL — "no data" and "no signal" look identical on screen.

The queue legitimately reads *0 workable* some days. A dead scraper also reads
*0 workable*. A BD cannot tell these apart, and after the second empty morning
they stop opening the tab — the failure is invisible right up to the point the
product is abandoned.

This is not hypothetical. `cra_press.py` already has three agencies statically
blocked (`_STATICALLY_BLOCKED = ["CRISIL", "ICRA", "INFOMERICS"]`) and BSE
retired its debt search endpoints in Sep 2026. Sources rot here as a matter of
routine.

**Required:** every surface shows *last successful read* per source and says
plainly when a source is stale or down. An empty list must state which of the
two it is. A source that has not answered in 48 hours raises an alarm a human
actually receives — not a log line nobody reads.

---

## 3. SEVERE — unattended nightly commits deploy straight to production.

The cloud routine commits to `main`; Vercel and Render deploy from `main`. Tests
passing is not the same as the app working, and nobody is awake. One bad night
takes production down until someone notices in the morning.

**Required:** the nightly routine must verify the app boots and `/api/health`
answers before it pushes, and the run log must record what it deployed so a bad
night can be reverted by commit hash without investigation.

---

## 4. SEVERE — the monthly BD list is the product's whole promise, and it is a deadline.

Tab 2 hands four named people their month. If it is late, wrong, or silently
different from last month's logic, trust is gone in one cycle and does not come
back.

**Required:** generation is deterministic and re-runnable; the list is snapshot
and stored, not recomputed on view, so "why was I given this name" is always
answerable months later; if inputs are stale the list still generates and says
which inputs were stale rather than failing or quietly shrinking.

---

## 5. SEVERE — "every rating for this company" will under-report, silently.

Two independent causes: `cra_press.py` reads 4 of 7 agencies, and issuer name
matching is **exact-only by design** (loosening it misattributed bonds across
corporate-group siblings — REC Power Development inheriting REC Ltd's paper).
Coverage measured at 27 of 60 Mumbai NBFCs.

A BD who sees three ratings and assumes that is all of them will walk into a
meeting wrong. That is worse than showing nothing.

**Required:** never render a rating list as complete. State which agencies were
searched, which were unreachable, and that a name-match miss is possible. This
is a labelling requirement, not a coverage one — do not "fix" it by loosening
the match.

---

## 6. MODERATE — the LLM is a free tier in the critical path.

`llm.py` chains OpenRouter then TokenRouter, both free. Free tiers rate-limit,
change models and disappear; TokenRouter served exactly one model as of Sep 2026.
If the "why this matters" sentence is LLM-generated and the provider is down,
rows must not blank out or the tab looks broken.

**Required:** every LLM-produced field degrades to a rule-based sentence, never
to empty. The LLM improves the wording; it is never the reason a row exists.
Adding FreeLLMAPI (Phase 5) widens the fallback chain but does not change this.

---

## 7. MODERATE — no auth, so no accountability.

Per-BD assignment without logins means anyone can move anyone's leads and no
outcome is attributable. Phase 0 is blocked on the operator running
`supabase_schema.sql`; until then `saved_leads` is one shared list and
`lead_events` does not exist — which also means the outcome data that is
supposed to fit the winnability weights is not being collected at all.

**Required:** Phase 0 is not optional housekeeping. Every month it slips is a
month of outcome data permanently lost.

---

## 8. MODERATE — single operator, no monitoring, no runbook.

One person holds every credential and all the context. Nothing pages anyone.

**Required, cheaply:** a health endpoint that reports per-source freshness (not
just process liveness — `/api/health` today only proves the process is up), and
a written note of what to do when each source dies.

---

## What this changes right now

1. Phase 1 becomes **durable** news persistence, on the existing
   Postgres-with-SQLite-fallback pattern. Not plain SQLite.
2. Source freshness and the empty-vs-broken distinction are a **Phase 1
   deliverable**, not a later polish item.
3. The nightly routine gains a boot check before it pushes.
4. Phase 0 is escalated from "operator will get to it" to **blocking**.
