> **Superseded.** This is the early-September 2026 framing: the problem was
> arriving too late. Kept for history. The current plan is
> [ROADMAP_V3.md](ROADMAP_V3.md); the three framings side by side are in
> `roadmap-evolution.html`.

# ACER-IQ Roadmap V2

## What this tool does

ACER-IQ is a lead-generation and sales intelligence platform for ACER (Infomerics Valuation and Rating), a SEBI-registered credit rating agency operating in India. It helps the BD/sales team find companies that need credit rating services and prepare for pitch meetings.

## Core problem

A credit rating agency needs to find companies at the exact moment they need a rating. The window is 2 to 6 weeks: a board approves an NCD issue, a bank asks for a loan rating renewal, or a company drops its existing agency after a downgrade. By the time you cold-call from a directory, the mandate is already given to CRISIL or ICRA.

## The 5 modules

### 1. Signal Radar (priority: high, status: planned)
Monitors public signals that indicate a company needs a credit rating soon.

**Signals tracked:**
- NCD/bond board resolutions from BSE/NSE filings
- Rating withdrawals and downgrades by other CRAs
- Surveillance/renewal dates from published rating rationales
- Bank loan rating expirations

**Why it matters:** These are companies that WILL need a rating within weeks. Not "might someday" but "must, by regulation, before they can issue debt."

### 2. Market News (priority: high, status: live)
Tracks corporate announcements from NSE that signal upcoming funding needs.

**What it captures:**
- Expansion plans (new plant, capacity increase)
- Capex approvals
- Fund raising resolutions (NCD, QIP, rights issue, private placement)
- Acquisitions and mergers
- Rating actions by other agencies
- Board meeting outcomes with financial resolutions

**Data source:** NSE Corporate Announcements API (real exchange filings, not predictions).

**Why it matters:** A company that announces a Rs 200 crore expansion will need debt. Debt needs a rating. You call them before the Big 3 agencies do.

### 3. Company Research (priority: medium, status: live, needs fixes)
Look up any Indian company and see its credit rating history across all SEBI-registered agencies.

**Current coverage:** 7 CRAs (CRISIL, ICRA, CARE Ratings, India Ratings, Acuite, Brickwork, Infomerics)

**Planned fix:** Add ACER as the 8th agency using internal data. This prevents pitching your own clients and surfaces renewal opportunities.

**Other fixes needed:**
- Surface errors instead of showing empty results when BSE/NSE is unreachable
- Fix the incorporation date bug (currently shows BSE listing date)
- Add pitch brief export (one-page PDF for meetings)

### 4. Company Directory (priority: low, status: live, renamed from "Find Leads")
Browse the registry of 12,800+ Indian financial entities from RBI official lists and NSE-listed companies.

**Honest assessment:** This is a lookup tool, not a lead generator. A list of NBFCs in Pune does not tell you who needs a rating. Keep it as a reference tool but do not position it as lead-gen.

**Fixes needed:**
- Ingest MCA company master for Corporate segment (currently returns junk from OpenStreetMap)
- Automate monthly registry refresh (currently manual CLI command)

### 5. My Pipeline (priority: medium, status: planned)
CRM-lite tracker so leads from other tabs do not die in a spreadsheet.

**Stages:** Identified > Contacted > Meeting Done > Proposal Sent > Mandated > Lost

**Features:** Source tagging (which signal found it), notes, follow-up dates, weekly summary.

## Build timeline

| Weeks | Phase | Work |
|-------|-------|------|
| 1-2 | Signal Engine | Market News tab (done). NCD/bond announcement scraper. |
| 3-4 | Signal Engine | Rating withdrawal/downgrade monitor across 7 CRA websites. |
| 5-6 | Signal Engine | Surveillance date extractor from rating rationale PDFs. |
| 7-8 | Fix + Harden | Add ACER as 8th CRA. Fix silent errors. Fix listing date bug. |
| 9-10 | Fix + Harden | MCA company master for Corporates. Automate registry refresh. |
| 11-12 | Fix + Harden | Pipeline tab. Auth. CORS lockdown. Move off free tier. |
| 13-16 | Scale | Pitch brief PDF export. Contact enrichment. Weekly digest email. |

## Known challenges

1. **BSE/NSE APIs break often.** Already have circuit breakers but need visible "last refreshed" timestamps.
2. ~~**MCA data access costs money.**~~ **Wrong.** The company master is free on
   data.gov.in, per Registrar of Companies. Corrected in V3.
3. **Rating rationale PDFs are unstructured.** Each CRA formats differently. Extracting surveillance dates needs PDF parsing and possibly LLM extraction.
4. **ACER's own data must come from ACER.** No public source has the complete internal rating book.
5. **Contact info for Indian mid-caps is hard.** Hunter.io does not work well for Indian SMEs. BSE CorpInfo has some directors but not always CFO emails.
6. **News feed will be noisy.** Keyword matching catches irrelevant results. Needs sector filters and relevance scoring.

## Data sources

| Source | Used for | Access |
|--------|----------|--------|
| RBI NBFC/UCB lists | Registry (banks, NBFCs, ARCs) | Free, public XLSX/PDF |
| NSE equity master | Registry (listed companies) | Free, public CSV |
| NSE corporate announcements | Market news, rating actions | Free API (needs cookie warmup) |
| BSE debt search | Past instruments, rating history | Free API (rate limited) |
| BSE CorpInfo | Directors, CIN, listing info | Free API |
| MCA company master | Corporate segment registry | **Free** - data.gov.in |
| CRA press releases | Rating withdrawals, surveillance dates | Free, needs per-CRA scraper |
| ACER internal database | ACER's own ratings | Internal, ACER must provide |
