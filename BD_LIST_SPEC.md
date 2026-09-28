# BD List + Pipeline — spec from the ACER Head of BD (2026-09-29)

> **Section 3 superseded by the operator the same day — see §3.** Everything
> else stands.

Decided in a plan review (gstack plan-ceo-review, mode: HOLD SCOPE) with the
Head of BD as reviewer. **Build to this; do not re-litigate it.** Engineering
non-negotiables (no dummy data, sources + read dates, reasons, empty vs broken,
frozen deterministic snapshot) still apply on top.

## 1. Split — hybrid (segment first, then score)
Up to 10 names per BD from their own segment, ranked by score. Segment owners
(editable in `backend/data/bd_roster.json`):
- **Hema** — NBFC / HFC / MFI, incl. securitisation/PTC
- **Avinash** — manufacturing & large corporates (NCD/CP)
- **Akash** — infra, real estate, power, EPC
- **Udit** — SME & bank loan ratings (BLR), first-time issuers

`Unclassified` names go to a shared pool, snake-drafted by score to fill any
BD below 10. Minimum score floor; **never pad** — "7 of 10: segment thin this
month". Ties by name (deterministic).

## 2. Row fields
Mandatory: `company_name`, `cin` ("CIN not found" if unknown, never blank),
`segment` (+ "inferred from name"), `instrument` ∈ {NCD, CP, BLR-LT, BLR-ST,
Securitisation/PTC, Other}, `trigger` ∈ {debt_raise, refinance, rating_action,
thin_coverage, capex, deal, macro}, `reason` (1 sentence), `play`,
`sources[]` (url + read_at), `score`, `urgency` ∈ {This week, This month},
`contact_route`, `rank`.
Optional, gap labelled: `size_cr` ("size not known", never estimated),
`current_agency` + `latest_rating` ("not visible (CRISIL/ICRA/Infomerics not
in feed)" — distinct from "unrated"), `maturity_date` ("BSE unavailable").
Also: `acer_history` (previously pitched / lost + reason), `carried_over`.
Urgency = This week if a debt raise is live, maturity ≤ 60 days, or a rating
action ≤ 14 days old; else This month.
Contact route ∈ {RBI-registry email (NBFC only), Company website/IR, Via
lender/banker, Via arranger, BD network} — the tool fills only a route it can
actually source.

## 3. ~~Mandatory fields per stage move~~ — SUPERSEDED 2026-09-29 (operator)

**Operator override:** "no need of those stages, just list the companies with
2-3 statuses." The Head of BD then decided, within that rule:
- **Statuses:** `Pending` / `In progress` / `Closed`. Choosing Closed forces
  one tap: **Won** or **Lost** (mandatory — month-end outcomes need it).
  Lost may carry an optional reason ∈ {Price, TAT, Went to other agency,
  Issuer deferred, No response}.
- **Recorded on a change:** nothing the BD must type. The system stamps who and
  when; an optional one-line note.
- **Overdue → staleness** (no date field): Pending with no change > 7 days,
  In progress with no change > 14 days.
- Self-sourced leads and Admin reassign: kept.
- Stored stages map: Pending=Identified, In progress=Contacted,
  Won=Mandated, Lost=Lost (the DB constraint and reports are unchanged).
- **Admin "all BDs" view**, per company: BD, company, instrument, urgency,
  trigger, status, Won/Lost, last updated, reason on hover, self-sourced
  tag; per BD at the top: count per status, % of list touched, Won count,
  stale count; clicking a BD filters to their list.

Original section 3, kept for the record (no longer built):
- **Contacted**: contact_name, designation, channel ∈ {Email, Call, In-person,
  Via banker, LinkedIn}, contact_date, next_followup_date
- **Meeting**: meeting_date, attendees_client, attendees_acer,
  instrument_discussed, next_followup_date
- **Proposal**: proposal_date, instrument, size_cr, fee_quoted_rs,
  competing_agency (Unknown allowed), next_followup_date
- **Mandated**: mandate_date, instrument, size_cr, fee_agreed_rs, mandate_ref
- **Lost**: lost_reason ∈ {Price, Turnaround time, Lost to another CRA,
  Existing agency retained, Issuer deferred/dropped issue, Banker/lender
  preference, Credit concern (ACER declined), No response after 3 attempts,
  Not a fit}; lost_to ∈ {CRISIL, ICRA, CARE, India Ratings, Acuite,
  Infomerics, Other} when a competitor won; note.
Self-sourced names: yes — `origin=self_sourced`, still need CIN + one-sentence
reason; source "BD self-sourced" + date; count in funnel, not in coverage.

## 4. Admin view
Per BD on one screen: assigned vs touched (coverage %), counts per stage,
conversion Contacted→Proposal and Proposal→Mandated, Rs cr + fees in Proposal
and Mandated, overdue follow-ups (name + days late), lost-reason breakdown,
self-sourced count. Stale-inputs banner. Clash flag when two BDs work the same
company/CIN. **Reassign: Admin only**, with a reason, logged as an event;
history moves with the name.

## 5. Month end
Outcome = highest stage reached by the last day of the month, frozen into the
snapshot's `outcomes`, plus `touched` (any move beyond Identified).
Rollover: in-progress names (Contacted→Proposal) stay with their owner and do
not use the 10 new slots. Untouched names do not auto-roll: they return to the
pool and compete on score; if picked again they are `carried_over` and go to a
**different** BD (deterministic).

## Out of scope for now
CFO contact database / scraping; sending email/WhatsApp from the tool; LLM
outreach drafts in the list (acer-scout does that on demand); auto-tuning
weights (wait for 3 months of outcomes); fee forecasting / incentives;
CRISIL/ICRA/Infomerics feeds; BSE workaround; mobile app.
