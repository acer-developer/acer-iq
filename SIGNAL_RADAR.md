# Signal Radar — Roadmap

## What it is

Signal Radar is the "what do I do today?" tab for an ACER business development head. It answers three questions every morning:

1. Which companies need a rating in the next 2 to 6 weeks?
2. Which sectors are in a phase where mandates are moving?
3. What playbook should I run for each situation?

Signal Radar sits above Market News. Market News is the raw feed. Signal Radar is the curated, prioritised, action-tagged view.

## Design principle

A BD head does not want a firehose. They want a prioritised list with a next action attached to each item.

Signal Radar prioritises signals by strength:

- **Very high** — public regulatory action (rating withdrawn, downgrade to negative watch, board resolution for NCD)
- **High** — announced capex or expansion above Rs 100 crore, first-time issuer indication
- **Medium** — sector-wide tailwind (results season, RBI policy), broad fund-raising activity
- **Low** — general market chatter

Each signal has a suggested play attached to it. Never show a signal without a suggested action.

## V1 (this build)

Three sections, top to bottom:

### 1. Playbook (collapsible)
Four strategic cards showing what to do in each market phase. Reads like a coaching guide, not a wall of text.

- **Sector on the rise** — new debt raises expected. Chase debut issuers before Big 3 lock them in.
- **Sector under stress** — rating downgrades and withdrawals happen. Chase displaced clients.
- **Stable sector** — surveillance renewals dominate. Chase price-sensitive incumbents.
- **First-time issuers** — no CRA relationship yet. Highest conversion, lowest cost of acquisition.

Each card has:
- The situation
- Why it happens
- The ACER play
- What signal to look for in Market News

### 2. Priority signals this week
A curated view derived from Market News that focuses on the highest-value categories only:

- Fund raise signals (NCD, bond, QIP, rights issue)
- Rating actions by other CRAs (withdrawals, downgrades)
- Large expansion/capex announcements

Everything else (board approvals with no financial resolution, general acquisitions, market moves) is hidden.

### 3. Coming soon
Cards for planned features so the user knows what's in the pipeline.

## V2 (weeks 3 to 6)

### Rating withdrawal monitor
Scrape the "Rating actions" page on all seven CRA websites weekly. Flag any company where the CRA rating was withdrawn. These are companies that MUST re-rate within 90 days by regulation.

Sources:
- CRISIL: crisilratings.com/en/home/rating-actions.html
- ICRA: icra.in/RatingRationale
- CareEdge: careratings.com/rating-rationales-details
- India Ratings: indiaratings.co.in/pressrelease
- Acuite: acuite.in/rating-rationale
- Brickwork: brickworkratings.com/rating-rationales
- Infomerics: infomerics.com/rating-rationale

Each CRA formats differently. Needs per-CRA scraper.

### Surveillance date extractor
Rating rationales published by CRAs mention the next surveillance date. Extract this from the PDF and build a calendar of upcoming renewal opportunities.

Needs PDF parsing (pypdf works) plus possibly LLM extraction for the free-text portions.

### Sector momentum score
Aggregate Market News signals by sector (using company-sector mapping from NSE). Show which sectors are hot this month (high fund-raise + acquisition activity) vs which are stressed (high rating action activity).

Feeds directly into the playbook: if renewables is hot, chase renewables issuers.

## V3 (weeks 7 to 12)

- Bank loan rating expiration tracker (BLRs expire annually, RBI-mandated review)
- SEBI EBP platform bidding signals (companies with upcoming bond auctions)
- Named target list: Brickwork's surviving clients (from beachhead #2 in CRA Intel)
- Auto-assign signals to BD owners based on sector coverage

## Data flow

```
Market News API  ─►  Priority filter  ─►  Signal Radar
                        │
                        ├─ fund_raise + rating_action + large expansion only
                        ├─ Deduplicate by company
                        └─ Sort by signal strength

CRA press releases  ─►  Weekly scraper  ─►  Withdrawal alerts (V2)

CRA rating PDFs  ─►  PDF parser  ─►  Surveillance calendar (V2)
```

## What NOT to build here

- Do not duplicate Market News. Signal Radar is a curated view, not a second feed.
- Do not add CRM features. Those go in My Pipeline.
- Do not add explanatory text longer than three lines per card. This is a working tab, not a reading tab.

## Success metric

If a BD person opens Signal Radar first thing in the morning, sees five prioritised signals, and knows which two to call before lunch — the tab has done its job.
