-- ACER-IQ Supabase schema
--
-- Paste this into the Supabase SQL Editor and run it:
--   https://supabase.com/dashboard/project/gmqyelarfyqfsyqvrvzj/sql/new
--
-- It cannot be run from the app: the publishable (anon) key has no DDL rights,
-- which is correct and should stay that way.
--
-- Until this runs, the app keeps using SQLite and nothing breaks - backend/
-- database.py falls through to SQLite whenever Supabase errors.

-- ---------------------------------------------------------------------------
-- searches: lets /api/export/{id} still work after a restart
-- ---------------------------------------------------------------------------
create table if not exists public.searches (
    id         text primary key,
    city       text,
    industry   text,
    results    text not null,          -- JSON array of company dicts
    saved_at   timestamptz not null default now()
);

create index if not exists idx_searches_saved_at on public.searches (saved_at desc);


-- ---------------------------------------------------------------------------
-- saved_leads: the BD pipeline, per user
--
-- user_id is what SQLite cannot give us and is the whole reason to move here.
-- It defaults to the caller's auth id so a client cannot write a row into
-- someone else's pipeline even if it tries.
-- ---------------------------------------------------------------------------
create table if not exists public.saved_leads (
    company_name text        not null,
    user_id      uuid        not null default auth.uid() references auth.users (id) on delete cascade,
    stage        text        not null default 'Identified'
                 check (stage in ('Identified','Contacted','Meeting','Proposal','Mandated','Lost')),
    winnability  integer,
    flags        jsonb       default '{}'::jsonb,
    agencies     jsonb       default '[]'::jsonb,
    notes        text        default '',
    saved_at     timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    primary key (user_id, company_name)
);

-- Added 2026-09-29: CIN is the only true company identity, so a lead saved
-- under two spellings still collapses to one row when a source gave a CIN.
alter table public.saved_leads add column if not exists cin text;


-- ---------------------------------------------------------------------------
-- lead_events: the outcome log
--
-- This is what eventually lets the winnability weights be fitted to what
-- actually converts, instead of staying flat guesses. Rows are never updated,
-- only appended - including when a lead is removed, because worked-and-dropped
-- is exactly the outcome data the weights need.
-- ---------------------------------------------------------------------------
create table if not exists public.lead_events (
    id           bigserial primary key,
    company_name text        not null,
    user_id      uuid        not null default auth.uid() references auth.users (id) on delete cascade,
    event        text        not null,     -- 'saved' | 'stage' | 'note' | 'removed'
    detail       text        default '',
    winnability  integer,
    flags        jsonb       default '{}'::jsonb,
    at           timestamptz not null default now()
);

create index if not exists idx_events_company on public.lead_events (company_name);
create index if not exists idx_events_user    on public.lead_events (user_id, at desc);


-- ---------------------------------------------------------------------------
-- cra_actions: the archive of CRA rating actions (backend/pipeline/action_history.py)
--
-- The feeds are latest-page snapshots, so this table is the only place the
-- history exists. Until it is created the backend keeps it in pipeline.sqlite,
-- which Render wipes on every restart (PREMORTEM.md section 1).
-- Append-only: the natural key dedupes re-fetches; nothing updates or deletes.
-- `date` stays text (DD-MM-YYYY), the format every scraper writes.
-- ---------------------------------------------------------------------------
create table if not exists public.cra_actions (
    agency       text        not null,
    company_name text        not null,
    rating       text        not null default '',
    action       text        not null default '',
    date         text        not null default '',
    isin         text        not null default '',
    source_url   text        not null default '',
    first_seen   timestamptz not null default now(),
    primary key (agency, company_name, rating, action, date)
);

create index if not exists idx_cra_actions_first_seen on public.cra_actions (first_seen);


-- ---------------------------------------------------------------------------
-- news_archive: every news item read, kept (backend/pipeline/news_archive.py)
--
-- Append-only. `first_seen` is the date ACER-IQ read the item - the read
-- date every row on screen carries. Classification happens on read, so the
-- raw text is what is stored.
-- ---------------------------------------------------------------------------
create table if not exists public.news_archive (
    key          text        primary key,
    source       text        not null,
    company      text        not null default '',
    symbol       text        not null default '',
    date         text        not null default '',   -- YYYY-MM-DD as published
    subject      text        not null default '',
    description  text        not null default '',
    categories   text        not null default '[]', -- JSON list
    link         text        not null default '',
    first_seen   timestamptz not null default now()
);

create index if not exists idx_news_first_seen on public.news_archive (first_seen);


-- ---------------------------------------------------------------------------
-- source_reads: last successful read per source (backend/pipeline/source_health.py)
--
-- What lets an empty list say "source unreachable since X" instead of looking
-- like a quiet day, and survive a restart while saying it (PREMORTEM section 2).
-- ---------------------------------------------------------------------------
create table if not exists public.source_reads (
    source        text primary key,
    last_attempt  timestamptz,
    last_success  timestamptz,
    failing_since timestamptz,
    last_error    text default '',
    last_count    integer
);


-- ---------------------------------------------------------------------------
-- Row level security
--
-- Without this, any holder of the publishable key could read every user's
-- pipeline. The key is meant to be public, so RLS is the only thing standing
-- between it and the data - do not disable it.
-- ---------------------------------------------------------------------------
alter table public.saved_leads enable row level security;
alter table public.lead_events enable row level security;
alter table public.searches    enable row level security;
alter table public.cra_actions enable row level security;
alter table public.news_archive enable row level security;
alter table public.source_reads enable row level security;

drop policy if exists "own leads" on public.saved_leads;
create policy "own leads" on public.saved_leads
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists "own events" on public.lead_events;
create policy "own events" on public.lead_events
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- Searches are shared working data, not personal: any signed-in user may read
-- and write them. Anonymous callers get nothing.
drop policy if exists "signed in searches" on public.searches;
create policy "signed in searches" on public.searches
    for all
    to authenticated
    using (true)
    with check (true);


-- CRA actions are public press releases, and the backend writes them with the
-- publishable key, so anon may read and insert. There is deliberately no
-- update or delete policy: the archive is append-only even to its own writer.
-- Caveat: anyone holding the publishable key can insert rows. Moving the
-- backend to the service-role key (server-side only) would close that.
drop policy if exists "read cra actions" on public.cra_actions;
create policy "read cra actions" on public.cra_actions
    for select
    to anon, authenticated
    using (true);

drop policy if exists "append cra actions" on public.cra_actions;
create policy "append cra actions" on public.cra_actions
    for insert
    to anon, authenticated
    with check (true);


-- News items are public publications: read + append only, like cra_actions.
drop policy if exists "read news archive" on public.news_archive;
create policy "read news archive" on public.news_archive
    for select to anon, authenticated using (true);

drop policy if exists "append news archive" on public.news_archive;
create policy "append news archive" on public.news_archive
    for insert to anon, authenticated with check (true);

-- Source freshness is one row per source, overwritten on every read, so it
-- needs update as well. It holds no personal data - only when a public site
-- last answered.
drop policy if exists "read source reads" on public.source_reads;
create policy "read source reads" on public.source_reads
    for select to anon, authenticated using (true);

drop policy if exists "write source reads" on public.source_reads;
create policy "write source reads" on public.source_reads
    for insert to anon, authenticated with check (true);

drop policy if exists "update source reads" on public.source_reads;
create policy "update source reads" on public.source_reads
    for update to anon, authenticated using (true) with check (true);


-- ---------------------------------------------------------------------------
-- After running this:
--   1. Authentication > Providers - enable Email (and Google if you want it).
--   2. Authentication > URL Configuration - add the app's URL to redirects.
--   3. Tell me it is done and I will wire the login screen and switch the
--      pipeline from SQLite to per-user Supabase rows.
-- ---------------------------------------------------------------------------
