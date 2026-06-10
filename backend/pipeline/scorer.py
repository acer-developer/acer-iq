from backend.pipeline.llm import chat, parse_json

SCORE_PROMPT = """\
You are a business development analyst at ACER, an Indian SEBI-registered credit rating agency.
Given this company profile, score it 0-100 on likelihood of needing credit rating services for {instrument_type}.

Company: {name}
Entity Type: {entity_type}
Target Instrument: {instrument_type}
City: {city}
CIN: {cin}
Incorporated: {incorporation_date}
BSE Instruments Found: {instrument_count}
Already rated by ACER: {acer_rated}
Currently rated by: {rated_by}

Context:
- Banks need ratings for AT1 bonds, tier-2 bonds, infrastructure bonds
- NBFCs need ratings for NCDs, commercial paper, securitisation
- Corporates need ratings for NCDs, bonds, commercial paper, IPO grading

Respond ONLY in this exact JSON (no markdown, no extra text):
{{
  "score": 85,
  "score_label": "Hot Lead",
  "why_quality_lead": ["specific reason 1", "specific reason 2", "specific reason 3"],
  "pain_points": ["specific pain 1", "specific pain 2"],
  "recommended_approach": "One specific pitch approach for this company"
}}

score_label: 80-100="Hot Lead", 60-79="Warm Lead", 40-59="Potential", 0-39="Low Priority"\
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


async def score_company(
    company: dict,
    industry: str,
    city: str,
    entity_type: str = "Financial Entity",
    instrument_type: str = "All",
) -> dict:
    # Always run rule-based first — gives real data-driven scores immediately
    company = _rule_based_score(company)

    # If OpenRouter key is configured, try to enrich with AI
    instruments  = company.get("past_instruments", [])
    rated_by     = list({
        inst.get("rating_agency", "") for inst in instruments
        if inst.get("rating_agency")
    })
    acer_rated   = any(
        any(a in r.upper() for a in ("INFOMERICS", "IVR", "ACER"))
        for r in rated_by
    )

    prompt = SCORE_PROMPT.format(
        name=company.get("name", ""),
        entity_type=entity_type,
        instrument_type=instrument_type,
        city=city,
        cin=company.get("cin", "N/A"),
        incorporation_date=company.get("incorporation_date", "N/A"),
        instrument_count=len(instruments),
        acer_rated="Yes" if acer_rated else "No",
        rated_by=", ".join(rated_by[:4]) if rated_by else "None found on BSE",
    )

    raw    = await chat(prompt, max_tokens=512)
    parsed = parse_json(raw) if raw else None

    if parsed and isinstance(parsed.get("score"), (int, float)):
        score = int(parsed["score"])
        company.update({
            "score":                score,
            "score_label":          _label(score),
            "why_quality_lead":     parsed.get("why_quality_lead", company["why_quality_lead"]),
            "pain_points":          parsed.get("pain_points", company["pain_points"]),
            "recommended_approach": parsed.get("recommended_approach", company["recommended_approach"]),
        })

    return company
