import logging

from backend.netutil import SourceStatus
from backend.pipeline.llm import chat, parse_json

log = logging.getLogger("acer_iq.scorer")

# One prompt scores the whole search (P4 fix: 1 LLM call per search, not 30)
BATCH_SCORE_PROMPT = """\
You are a business development analyst at ACER, an Indian SEBI-registered credit rating agency.
Score each company below 0-100 on likelihood of needing credit rating services for {instrument_type} in {city}.

Context:
- Banks need ratings for AT1 bonds, tier-2 bonds, infrastructure bonds
- NBFCs need ratings for NCDs, commercial paper, securitisation
- Corporates need ratings for NCDs, bonds, commercial paper, IPO grading
- "Already rated by ACER" = renewal/cross-sell; "rated by competitors" = second-opinion pitch

Companies (index | name | type | RBI layer/sub-type | BSE instruments | rated by | ACER client):
{company_lines}

Respond ONLY with a JSON array, one object per company, no markdown:
[
  {{"i": 0, "score": 85, "why_quality_lead": ["reason 1", "reason 2"],
    "pain_points": ["pain 1"], "recommended_approach": "one specific pitch"}}
]
"""


def _label(score: int) -> str:
    if score >= 80: return "Hot Lead"
    if score >= 60: return "Warm Lead"
    if score >= 40: return "Potential"
    return "Low Priority"


def _rule_based_score(company: dict) -> dict:
    """
    Real scoring based on actual company data — no LLM needed.
    Uses BSE instrument history, entity type, incorporation year, and
    competitor agency coverage to produce a meaningful lead score.
    """
    score = 30
    reasons: list[str] = []
    pain_points: list[str] = []

    name        = company.get("name", "Unknown")
    entity      = company.get("entity_type", "Corporate")
    instruments = company.get("past_instruments", [])
    inc_date    = company.get("incorporation_date", "")
    cin         = company.get("cin", "")

    # ── Entity type relevance ─────────────────────────────────────────────────
    if entity == "NBFC":
        score += 20
        reasons.append("NBFCs are ACER's primary segment — strong NCD, CP and securitisation mandate potential")
        pain_points.append("NBFCs without multiple agency ratings face higher cost of institutional funds")
        layer = company.get("registry_layer", "")
        if layer in ("Upper", "Top"):
            score += 12
            reasons.append(f"RBI {layer}-layer NBFC — large regulated balance sheet, mandatory market borrowing programs")
        elif layer == "Middle":
            score += 7
            reasons.append("RBI Middle-layer NBFC — sizeable book, active institutional funding needs")
        if company.get("deposit_taking"):
            score += 5
            reasons.append("Deposit-taking NBFC — FD ratings required under RBI norms")
    elif entity == "Bank":
        score += 15
        reasons.append("Banks issue AT1 bonds, Tier-2 bonds and infrastructure bonds — require CRA ratings")
        pain_points.append("Regulatory requirements push banks to seek multiple agency opinions for large issuances")
        sub = company.get("registry_sub_type", "")
        if sub == "Scheduled UCB":
            score += 8
            reasons.append("Scheduled urban co-operative bank — Tier-2 bond and FD rating requirements, ACER's sweet spot")
        elif sub == "Small Finance Bank":
            score += 8
            reasons.append("Small finance bank — regular Tier-2 and refinance instrument ratings needed")
    elif entity == "Corporate":
        score += 10
        reasons.append("Corporates increasingly prefer rated NCDs over bank loans for cost-efficient debt")
        pain_points.append("Unrated corporates face a limited investor universe and higher borrowing spreads")

    # ── BSE instrument history ────────────────────────────────────────────────
    n = len(instruments)
    if n >= 6:
        score += 28
        reasons.append(f"Active debt issuer — {n} instruments on BSE; ongoing rating mandate likely")
    elif n >= 3:
        score += 18
        reasons.append(f"{n} BSE-listed instruments — established debt market participant")
    elif n >= 1:
        score += 10
        reasons.append(f"{n} BSE instrument(s) found — has experience with rated debt products")
        pain_points.append("Limited BSE instrument history — opportunity to expand their rating coverage")
    else:
        score += 4
        reasons.append("No BSE instruments found — potential first-time rating mandate opportunity")
        pain_points.append("Currently absent from rated debt markets — high-value new business target")

    # ── ACER coverage ─────────────────────────────────────────────────────────
    acer_rated = any(
        any(a in inst.get("rating_agency", "").upper()
            for a in ("INFOMERICS", "IVR", "ACER"))
        for inst in instruments
    )
    if acer_rated:
        score = max(score, 78)
        reasons.append("Already rated by ACER/Infomerics — renewal, upgrade and cross-sell opportunity")
    else:
        reasons.append("Not yet rated by ACER — clear new mandate with no internal conflict")

    # ── Competitor coverage ───────────────────────────────────────────────────
    competitor_set: set[str] = set()
    for inst in instruments:
        ag = inst.get("rating_agency", "").upper()
        for a, label in [("CRISIL", "CRISIL"), ("ICRA", "ICRA"), ("CARE", "CARE Ratings"),
                         ("INDIA RATINGS", "India Ratings"), ("ACUIT", "Acuité"),
                         ("BRICKWORK", "Brickwork"), ("BWR", "Brickwork")]:
            if a in ag:
                competitor_set.add(label)
    if competitor_set:
        score += 8
        comps = ", ".join(sorted(competitor_set)[:2])
        reasons.append(f"Rated by {comps} — proven mandate buyer; ACER can offer a competitive second opinion")
        pain_points.append(f"Existing relationship with {comps} — needs a clear ACER differentiation pitch")

    # ── Incorporation age ─────────────────────────────────────────────────────
    if inc_date:
        try:
            from datetime import date
            year = int(str(inc_date)[:4])
            age  = date.today().year - year
            if age >= 15:
                score += 8
                reasons.append(f"Established company (incorporated {year}) — proven track record supports rating eligibility")
            elif age >= 5:
                score += 4
        except Exception:
            pass

    # ── CIN verified ─────────────────────────────────────────────────────────
    if cin:
        score += 3

    score = min(score, 97)

    # ── Recommended approach ─────────────────────────────────────────────────
    if acer_rated:
        approach = "Existing ACER client — schedule renewal meeting and explore additional instrument mandates"
    elif n >= 4 and competitor_set:
        approach = f"Pitch ACER as a complementary rating alongside {sorted(competitor_set)[0]} — highlight faster TAT and competitive fees"
    elif n >= 1:
        approach = "Approach CFO/Treasury with ACER's sector expertise and turnaround advantage for their existing debt program"
    else:
        approach = "Lead with ACER's first-time rating package — cost-benefit of accessing institutional debt markets"

    company.update({
        "score":                score,
        "score_label":          _label(score),
        "why_quality_lead":     reasons[:4],
        "pain_points":          pain_points[:3],
        "recommended_approach": approach,
    })
    return company


def _company_line(i: int, company: dict) -> str:
    instruments = company.get("past_instruments", [])
    rated_by = sorted({
        inst.get("rating_agency", "") for inst in instruments
        if inst.get("rating_agency")
    })
    acer = any(
        any(a in r.upper() for a in ("INFOMERICS", "IVR", "ACER")) for r in rated_by
    )
    sub = company.get("registry_layer") or company.get("registry_sub_type") or "-"
    return (f"{i} | {company.get('name', '?')} | {company.get('entity_type', '?')} | {sub} | "
            f"{len(instruments)} | {', '.join(rated_by[:3]) or 'none'} | "
            f"{'yes' if acer else 'no'}")


def apply_batch_scores(companies: list[dict], parsed) -> int:
    """Apply a parsed batch-LLM response onto companies. Returns #applied."""
    if not isinstance(parsed, list):
        return 0
    applied = 0
    for item in parsed:
        if not isinstance(item, dict):
            continue
        i = item.get("i")
        score = item.get("score")
        if not isinstance(i, int) or not 0 <= i < len(companies):
            continue
        if not isinstance(score, (int, float)):
            continue
        c = companies[i]
        c.update({
            "score":                int(score),
            "score_label":          _label(int(score)),
            "why_quality_lead":     item.get("why_quality_lead") or c.get("why_quality_lead", []),
            "pain_points":          item.get("pain_points") or c.get("pain_points", []),
            "recommended_approach": item.get("recommended_approach") or c.get("recommended_approach", ""),
        })
        applied += 1
    return applied


async def score_companies(
    companies: list[dict],
    industry: str,
    city: str,
    instrument_type: str = "All",
    status: SourceStatus | None = None,
    max_llm_companies: int = 40,
) -> list[dict]:
    """
    Score a whole search result set: rule-based score for every company,
    then a SINGLE batched LLM call refines the top slice (if a key is set).
    """
    for c in companies:
        _rule_based_score(c)

    if not companies:
        return companies

    batch = companies[:max_llm_companies]
    lines = "\n".join(_company_line(i, c) for i, c in enumerate(batch))
    prompt = BATCH_SCORE_PROMPT.format(
        instrument_type=instrument_type, city=city, company_lines=lines,
    )
    raw = await chat(prompt, max_tokens=120 * len(batch), status=status)
    parsed = parse_json(raw) if raw else None
    applied = apply_batch_scores(batch, parsed)
    if raw and not applied:
        log.warning("batch LLM scoring returned unusable output (%d companies)", len(batch))
    elif applied:
        log.info("batch LLM scoring applied to %d/%d companies", applied, len(batch))

    return companies
