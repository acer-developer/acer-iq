# ACER-IQ — Build Plan (Aladdin reframe)

Written 2026-09-28. Supersedes the tab list in ROADMAP_V3.md; the winnability
premise in that document still stands and is not re-argued here.

## The reframe in one line

Stop being a lead list. Become the thing that reads the market each night and
tells four BDs, by name, who to call this month and why.

## The four tabs

| # | Tab | Answers | Status |
|---|---|---|---|
| 1 | **Macro** | A big thing happened — who does it hit, and do they now need a rating? | Build |
| 2 | **Monthly BD list** | What are my four BDs calling this month, and why each name? | Build |
| 3 | **News, kept** | What mattered to CRAs, stored forever, never discarded | Build |
| 4 | **Company deep-dive** | Everything about one company, all seven agencies | Deferred — see Blocker |

Dropped: the India map (`MapView.jsx`). It belonged to the retired
"find companies by city" framing.

## Non-negotiable invariants

These are build rules, not aspirations. A PR that breaks one does not land.

1. **No dummy data.** No mock fallbacks, no illustrative numbers, no seeded
   examples on screen. If a source is down the UI says so and shows nothing.
   (Already true in `backend/pipeline/` — keep it true.)
2. **Every figure carries a source.** A link to the page it came from, and the
   date it was read.
3. **Every item carries a reason.** One plain sentence: why this matters to
   ACER. No signal without a next action.
4. **Major only.** Tab 1 and Tab 3 filter for events with sector-level
   consequence. Routine board approvals and market chatter are not news.

## Order of work

Each phase is landable on its own and leaves the app working.
**`PROGRESS.md` is the live checklist — what is actually done is recorded there,
not here.** `PREMORTEM.md` is what this plan is defending against.

### Phase 0 — Supabase schema  *(blocked on operator)*
Run `supabase_schema.sql` in the SQL editor, enable Email auth, add the app URL
to redirects. Creates `searches`, `saved_leads`, `lead_events` with RLS.
Until this runs the app stays on SQLite as one shared list and nothing breaks.
Everything in Phase 4 needs `lead_events` to exist.

### Phase 1 — **Durable** news persistence  *(rewritten after the premortem)*
Today `market_news.py` and `rss_news.py` fetch live and discard on every
refresh. Only CRA rating actions are archived (`action_history.py`).

**Plain SQLite is not an option.** `backend/registry/data/pipeline.sqlite` is
gitignored and `render.yaml` declares no disk on a free plan, so every
production write is destroyed on the next restart — the archive would be a
silent lie. See PREMORTEM.md §1.

Put `news_archive` (and `action_history`) on the Postgres-with-SQLite-fallback
pattern **already implemented in `backend/database.py`** — reuse it, do not
write a second one. SQLite stays as the local-dev path only. Append-only,
dedupe key, `since(days)` and `stats()` helpers, offline self-check. Wire both
news fetchers to record on every poll.

Phase 1 also owns the empty-vs-broken distinction (PREMORTEM.md §2): a
`last_successful_read` per source, `/api/health` reporting per-source freshness
rather than just process liveness, and empty states that say which they are.
This is a Phase 1 deliverable, not later polish — a dead scraper and a quiet
day look identical otherwise, and that is how the tool gets abandoned.

Why first: nothing downstream can spot a trend in a feed that is thrown away.

### Phase 2 — Tab 3, Market News on the archive
Rebuild the tab to read the archive rather than the live call. Add the
major-only classifier. Every row renders source link + why-it-matters line.
Backfill starts accumulating from the day Phase 1 lands.

### Phase 3 — Tab 1, Macro
The first version is a join, not a model:

```
major macro event  ->  tag its sector  ->  companies in that sector with
                                            debt maturing < 9 months
                                            OR thin interest coverage
                                        ->  rank by existing winnability
                                        ->  named list + reason + source
```

All four inputs already exist: news classification, 2,126 NSE symbols with
filed financials (`fundamentals.py`), the refinance window (`refinance.py`),
and `winnability.py`. No new data source, no ML.

### Phase 4 — Tab 2, Monthly BD allocation
On the 1st, freeze a ranked list and split it across four named BDs. Each
assigned name carries signal, source, reason and suggested play. Frozen
matters: a live feed cannot be measured, a monthly list can. Month-end
outcomes append to `lead_events`, which is what eventually fits the
winnability weights to what actually converts.

### Phase 5 — FreeLLMAPI provider
`llm.py` already has a `_providers()` list and a `tokenrouter_base_url`
setting, and FreeLLMAPI is OpenAI-compatible, so this is a provider entry and
a config key. Caveat: it is a router you self-host, not a hosted free API —
it needs the same always-on box as the rest, and its own provider keys.

## The blocker behind Tab 4

`cra_press.py` reads 4 of 7 agencies: CRISIL, ICRA and Infomerics are blocked,
CARE works but lookup-only, Acuité / Brickwork / India Ratings give feeds.
"Every rating for this company" is not answerable today.

`production/acer-cra-tracker` exists to solve exactly this — same seven sites,
six stub adapters, and a better data model for it (`RatingAction` with dedupe
keys and supersede flags). Building both means writing the same scrapers twice.

**Open decision:** fold the tracker in as ACER-IQ's ingestion layer, or keep
them separate. Tab 4 does not start until this is settled.

## Hosting

Unresolved and now on the critical path — nightly routines, the archive and a
self-hosted FreeLLMAPI all want an always-on box. Oracle Cloud Always Free, or
~$7/mo. Everything else in this plan is free.
