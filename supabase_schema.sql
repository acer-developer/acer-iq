-- ACER-IQ Supabase schema
--
-- Paste this into the Supabase SQL Editor and run it:
--   https://supabase.com/dashboard/project/zkqwzivrskdtbuacjvdg/sql/new
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
-- Row level security
--
-- Without this, any holder of the publishable key could read every user's
-- pipeline. The key is meant to be public, so RLS is the only thing standing
-- between it and the data - do not disable it.
-- ---------------------------------------------------------------------------
alter table public.saved_leads enable row level security;
alter table public.lead_events enable row level security;
alter table public.searches    enable row level security;

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


-- ---------------------------------------------------------------------------
-- After running this:
--   1. Authentication > Providers - enable Email (and Google if you want it).
--   2. Authentication > URL Configuration - add the app's URL to redirects.
--   3. Tell me it is done and I will wire the login screen and switch the
--      pipeline from SQLite to per-user Supabase rows.
-- ---------------------------------------------------------------------------
