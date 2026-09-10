# ACER-IQ Roadmap V3

Supersedes ROADMAP_V2.md. The change in V3 is the premise: V2 assumed **finding**
companies that need a rating is the bottleneck. It isn't. Every CRA sees the same
downgrade at the same hour. The bottleneck is **winning** them, and the highest-value
work sits partly outside the software.

**The documents.** This file is the current plan. `TODO.md` is the same plan as a
working checklist. `ROADMAP.md` (V1) and `ROADMAP_V2.md` are retired and carry a
banner saying so - kept only for history. `roadmap-evolution.html` shows all three
framings side by side; its source is `roadmap-evolution.workflow.json`.
`PIPELINE_RADAR_SPEC.md` remains the detailed signal spec behind Phase 2.

---

## 0. In plain words

**The problem**

- ACER's sales team calls companies off a list, so they call too late. The company has
  already picked CRISIL or ICRA.
- When a company does come up, there is no way to tell a big one from a small one, or a
  likely yes from a definite no.
- Nobody writes down who was called or what happened, so nothing is learned.

**How we fix it**

- Watch for the moment a company needs a rating. The signs are public - a board approves
  a bond, another agency drops them, a rating comes up for renewal. Nobody at ACER reads
  these daily. The tool will.
- Only show companies ACER can realistically win: never rated before, unhappy with their
  current agency, or already using two agencies. Skip anyone comfortably rated by a big one.
- Check they can pay before anyone calls. Some companies leave their agency because they
  are in trouble. Those are not leads.
- Show it as a dashboard, with a login each. Anyone can save a company to their own list.
- Record what happened to each name. After six months the tool learns which signals
  actually turn into business.

**Two bigger things first, neither needs software**

- Get ACER onto more bank approved-agency lists. If ACER is not on the list, the bank
  cannot pick ACER. One bank added beats hundreds of cold calls.
- Check ACER's existing clients for who might leave. Keeping a client is cheaper than
  winning one, and that data already exists.

---

## 1. What we are solving

ACER's BD team works from directories, so it arrives after the mandate is given.
Three consequences:

1. **No trigger awareness.** The rating decision has a 2-6 week window with a public
   trigger (board NCD resolution, competitor withdrawal or downgrade, surveillance
   date falling due). Nobody at ACER reads all seven CRA sites daily.
2. **No sizing.** Even when a name surfaces, a Rs 5,000 cr borrower looks the same as
   a Rs 50 cr one, so priority is guesswork.
3. **No winnability filter.** An issuer happily rated by ICRA will not move to a small
   CRA regardless of timing. Most "leads" are unwinnable and burn calling capacity.

The scarce resource is not leads. It is **BD calling hours**. Thirty workable leads
beat three hundred unworkable ones.

---

## 2. Strategic frame

Ranked by expected value, highest first. Note that the top two need no new software.

### Lever 1 - Empanelment (no software)

Being on a bank's approved CRA panel is binary: not on it, cannot be chosen. Same for
PSU panels, state industrial corporations, SIDBI and MSME scheme lists. One empanelment
win outranks several hundred prospected issuers, because the branch then does the
selling. **Action: audit the panel map - which banks, which zones, where ACER is absent.**

### Lever 2 - Retention of the existing book (internal data)

Rating revenue is an annuity; surveillance renews annually. A lost client costs more
than a won one earns in year one. ACER has no churn model on its own portfolio: who is
late paying, who went quiet at last surveillance, who is shopping a competing quote.
Point the scoring machinery inward first - free data, higher yield, zero adverse
selection. **Action: churn audit on internal data.**

### Lever 3 - Winnability-ranked acquisition (this tool)

Score winnability, not need. The winnable pool is narrow and identifiable:

- unrated first-time borrowers
- issuers tagged Issuer Not Cooperating by their current CRA
- ratings withdrawn at the issuer's own request
- issuers already rated by two or more agencies (proven shoppers)

These four flags outrank the entire news feed. Channel-first where possible: most
volume is bank loan ratings routed through bank RMs, arrangers and CA firms, so
mapping twenty intermediaries beats prospecting two thousand issuers.

### Lever 4 - Positioning and inbound

A small CRA does not win on brand or accuracy. It wins on turnaround time, fee, and
willingness to service a facility the Big 3 price out of. That must be stated and
measured - if ACER cannot say "rated in N working days," the differentiation is
theoretical. Sector reports (Steel / Paper / HFC, already underway) are the right
inbound engine for exactly the mid-market segment where brand is the objection.

---

## 3. Non-negotiables (from the premortem)

These four decide whether the build survives contact with reality.

| Risk | Guard |
|---|---|
| **Adverse selection.** INC-tagged and self-withdrawn issuers often left because they could not pay. Growth by importing other agencies' defaults is negative value. | A credit screen sits **on top of** winnability. No lead reaches BD without it. |
| **Optics.** A machine that hunts rating-shoppers reads badly in a SEBI inspection. | Framed, named and logged as **coverage-gap analysis**. Never as shopping. |
| **Scraper rot.** Seven CRA sites, no APIs, silent breakage. | Source-health alarms that notify on failure. A dark pipeline must be loud. |
| **No feedback loop.** Until mandates close, the score is opinion with a number on it. | Outcome logging from day one. Every lead carries the signal that produced it. |

Two more that quietly kill adoption:

- **Delivery.** BD lives in email, WhatsApp and Excel. A dashboard they must remember to
  open can still die. The dashboard ships first (Phase 3) because logins and saved lists
  are what make outcome data possible; the email digest that pushes the queue to them is
  parked, not cancelled, and should follow soon after.
- **Entity resolution.** Joining MCA / NSE / BSE / CRA names is genuinely hard. Two bad
  matches in a demo and BD stops trusting everything. Budget real time for it and show
  match confidence in the UI.

---

## 4. Phases

### Phase 0 - Two audits, no code (~2 weeks)

Empanelment map and internal churn audit. Both answerable on existing data. Either could
be worth more than the entire software build; both change what the software should rank.

### Phase 1 - Trustworthy foundation

- Persist searches to Supabase (code path exists, needs keys and the table)
- Auth gate + CORS lockdown, backend and frontend shipped together
- Always-on host (Oracle Cloud Always Free ARM VM keeps this at zero; ~$7/mo buys less hassle)
- Source-health alarms on every scraper and API
- Outcome logging schema in place **before** any lead is generated

### Phase 2 - Winnability engine (the product)

- The four winnability flags: unrated first-timer, INC-tagged, self-withdrawn, multi-CRA
- CRA press-release scrapers for the above - the only way to see unlisted issuers and
  bank-loan ratings, which is most of ACER's addressable market. Try `httpx` per CRA
  first; reach for a headless browser only where one actually blocks
- Renewal calendar from published surveillance dates: a warm list built once, mined forever
- Credit screen layered on top, per the non-negotiables
- **Merge the signal layer with the `unaccepted-ratings` repo.** Both projects are
  solving the same problem twice today

### Phase 3 - Dashboard, logins, saved lists

- **Dashboard first.** One ranked queue, not five browse tabs. Signal chips show why each
  name surfaced; low-winnability names are suppressed rather than listed.
- **A login per BD user.** Supabase Auth is already a dependency, so this is email or
  Google sign-in plus a `saved_leads` table with row-level security - each user reads and
  writes only their own rows. No custom auth code, no session handling of our own.
- **Add and save.** One button on any lead saves it under that user's login and drops it
  into their pipeline at stage Identified.
- Pipeline tracker (Identified > Contacted > Meeting > Proposal > Mandated > Lost) with
  source tagging, so outcome data actually accumulates
- **Parked: the weekly digest email.** Still wanted, not now. Everything it needs - the
  ranked queue, per-user identity, the send list - falls out of the dashboard work above,
  so building the dashboard first costs the email nothing.
- ACER as the 8th agency, so we stop pitching our own clients
- Briefing surface: news attached to names already in the pipeline, not a global feed.
  Add free RSS (BusinessLine, Moneycontrol, Business Standard). This prepares the call;
  it does not rank the pipeline

### Phase 4 - Universe and sizing

- MCA company master from data.gov.in (**free** - correct the V2 note that calls it paid),
  geocoded once at ingest, replacing the OpenStreetMap guesswork in the Corporates segment
- Twelve Data free tier for fundamentals: debt quantum, interest coverage into the fit score
- Pitch brief PDF export

---

## 5. Explicitly not doing

- Global news feed as a lead source. It is a briefing surface.
- Paid news APIs. RSS is free and better here.
- Headless browser infrastructure until a CRA site actually blocks plain HTTP.
- A bigger company directory as an end in itself. Volume is not the problem.
- Rebuilding any of this as an Artifact - its CSP blocks all outbound fetch, so none of
  the data layer can run there. Artifacts are for read-only dashboards off data the
  backend already produced.

---

## 6. Cost

Everything above runs on free tiers: data.gov.in, NSE/BSE, CRA scrapes, Supabase free,
OpenRouter free models, Oracle Cloud Always Free. Hosting is the only line item and even
that is optional. From the public-apis survey, exactly one item is worth taking: a
fundamentals API for sizing.

---

## 7. Open questions

1. **Ordering.** Phase 4 (MCA ingest) is the cheapest real win and sits last, because a
   longer company list does not answer "who needs a rating this month." Reverse it if you
   would rather bank the easy one first.
2. **Regulatory specifics.** The RBI risk-weight and MSME-scheme points in Lever 2 are
   directionally right but unverified. Confirm current thresholds before committing.
3. **ACER internal data.** Levers 2 and 3, and the 8th-agency feature, all depend on ACER
   providing its own rating book. No public source has it. This is the single biggest
   external dependency in the plan.
4. **Bus factor.** One maintainer. Worth deciding now what happens to the scrapers when
   you are unavailable.

---

## Appendix - status ledger

**Working today:** company credit lookup across 7 CRAs (NSE rating-action disclosures
merged with BSE debt search, with an honest `data_status`); Market News from NSE corporate
announcements; sector indices; RSS news; registry of ~12,800 RBI/NSE entities; directors,
offices, past instruments; rule-based plus LLM fit scoring; circuit breakers on BSE/NSE;
frontend with all tabs shipped.

This ledger was stale and is corrected here. Signal Radar is **shipped**, not a
placeholder (`SignalRadarPage.jsx`, 553 lines), and so are the winnability engine
and the CRA press-release scrapers.

**Shipped since:** winnability engine + credit screen; CRA press scrapers (4 of 7
readable, 3 blocked by reCAPTCHA / CSRF / RSC and off-limits by policy); ranked
queue dashboard; saved leads + outcome log; **daily feed archive**
(`snapshot_store.py`), so the queue is no longer capped at page 1 of each feed;
**renewal calendar** (`renewal.py`); **maturing-NCD refinance list**
(`refinance.py`, 353 issuers live); **briefing surface** (per-company news
filtering, with BusinessLine and Business Standard added).

**Half-built:** Corporates discovery still served by the OpenStreetMap fallback;
search persistence code path exists but Supabase is not wired. Debt sizing now
arrives for issuers whose rationale PDF is readable, but not from a fundamentals
API.

**Deliberately dropped:** the pipeline tracker UI. ACER already runs a CRM, so a
second pipeline UI here would be a worse copy of it - phase 3 listed one only
because it assumed no CRM existed. The `/api/leads` backend and its outcome log
stay. **This leaves the "no feedback loop" non-negotiable open, not solved:**
without a tab or a CRM write-back, `lead_events` only ever logs `saved`, so the
winnability weights stay flat. See TODO.md.

**Not started:** MCA ingest; ACER as 8th agency; auth; always-on host; digest
email; pitch brief PDF; a fundamentals API.

**Coverage gap that matters most:** `credit_history.py` only sees listed companies,
because NSE disclosures and BSE debt search are its only inputs. Unlisted issuers and
bank-loan ratings - most of ACER's addressable market - are invisible until the CRA
press-release scrapers exist.
