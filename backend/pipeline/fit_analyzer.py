from backend.pipeline.llm import chat, parse_json

FIT_PROMPT = """\
You are a strategic business analyst at ACER (Infomerics Valuation and Rating Pvt. Ltd.), \
a SEBI-registered Indian credit rating agency.

Evaluate whether the following company is a strong target for ACER to pitch credit rating services.

Company: {name}
Entity Type: {entity_type}
Location: {address}
CIN: {cin}
Incorporated: {incorporation_date}
Total BSE-listed instruments found: {instrument_count}

Current credit rating coverage (from BSE data):
{rating_summary}

Number of agencies currently rating this company: {rated_by_count} out of 7

Respond ONLY in this exact JSON format (no markdown, no extra text):
{{
  "fit_score": 78,
  "fit_label": "Good Fit",
  "opportunity_type": "New NCD mandate",
  "key_insights": [
    "Specific insight about this company's rating situation",
    "Why ACER has an opportunity here",
    "What makes this company a priority"
  ],
  "watch_outs": [
    "Specific competitive or risk concern"
  ],
  "recommended_action": "Specific action for ACER sales team",
  "best_instrument_pitch": "NCD",
  "urgency": "High",
  "already_rated_by_infomerics": false
}}

fit_label: 80-100="Strong Fit", 60-79="Good Fit", 40-59="Moderate Fit", 0-39="Low Priority"
urgency: "High" | "Medium" | "Low"
"""


def _build_rating_summary(agencies: list[dict]) -> str:
    lines = []
    for ag in agencies:
        if ag["is_rated"]:
            lines.append(
                f"  - {ag['full_name']}: {ag['latest_rating']} "
                f"({ag['total_instruments']} instrument(s))"
            )
        else:
            lines.append(f"  - {ag['full_name']}: Not rated / no BSE data")
    return "\n".join(lines)


def _label(score: int) -> str:
    if score >= 80: return "Strong Fit"
    if score >= 60: return "Good Fit"
    if score >= 40: return "Moderate Fit"
    return "Low Priority"


def _data_driven_analysis(company: dict, credit_data: dict) -> dict:
    """
    Build a meaningful fit analysis entirely from real BSE data —
    no LLM needed. Uses actual agency coverage, instrument count, and
    entity type to generate specific insights.
    """
    agencies      = credit_data.get("agencies", [])
    rated_by      = credit_data.get("rated_by_count", 0)
    total         = credit_data.get("total_instruments", 0)
    entity        = company.get("entity_type", "Corporate")
    name          = company.get("name", "this company")
    raw_insts     = credit_data.get("raw_instruments", [])

    # ── Who IS rating them ────────────────────────────────────────────────────
    active_agencies = [
        ag["full_name"] for ag in agencies
        if ag["is_rated"] and ag["key"] != "INFOMERICS"
    ]
    acer_agency = next((ag for ag in agencies if ag["key"] == "INFOMERICS"), {})
    acer_is_rating = acer_agency.get("is_rated", False)

    # ── Score logic ───────────────────────────────────────────────────────────
    if acer_is_rating:
        score = 88
        opportunity = f"Existing ACER client — {acer_agency.get('total_instruments', 0)} active instruments"
        urgency = "High"
        insights = [
            f"Already rated by Infomerics/ACER with {acer_agency.get('total_instruments',0)} instrument(s) — renewal and upsell opportunity",
            f"Total {total} instruments across BSE — active debt issuer with ongoing rating needs",
            "Relationship already established — lowest-effort high-value account to grow",
        ]
        watch_outs = [
            "Ensure renewal pipeline is tracked and no mandate is lost to a competitor",
            f"{rated_by} agencies total — monitor if any competitor is being added or displacing ACER",
        ]
        best_pitch = "Renewal + additional instruments"

    elif rated_by == 0 and total == 0:
        score = 82
        opportunity = "First-time rating mandate — company has no BSE-listed rated instruments"
        urgency = "High"
        insights = [
            f"{name} has no BSE-listed rated instruments — clean slate opportunity for ACER as first mover",
            f"{entity} entities at this scale are increasingly moving to rated debt to reduce borrowing costs",
            "No competitive displacement required — ACER can build the entire rating relationship from scratch",
        ]
        watch_outs = [
            "Verify company is actually seeking debt financing before investing sales effort",
            "May need education on benefits of credit rating if first-time issuer",
        ]
        best_pitch = "NCD" if entity in ("NBFC", "Corporate") else "Bond"

    elif rated_by == 0 and total > 0:
        score = 78
        opportunity = f"{total} instruments on BSE but no agency coverage matched — possible data gap or private ratings"
        urgency = "High"
        insights = [
            f"{total} debt instruments found on BSE with no matching SEBI-registered agency — strong mandate gap",
            "ACER can be the first formal credit rating agency for this issuer",
            "Active debt program confirmed — company is already comfortable with capital markets",
        ]
        watch_outs = [
            "Cross-check if ratings exist from agencies not tracked on BSE",
            "Instruments may be privately placed and already rated — verify before outreach",
        ]
        best_pitch = "NCD" if entity in ("NBFC", "Corporate") else "Bond"

    elif rated_by <= 2:
        score = 70
        comps = ", ".join(active_agencies[:2]) if active_agencies else "existing agencies"
        opportunity = f"Secondary mandate — rated by {rated_by} of 7 agencies, whitespace for ACER"
        urgency = "Medium"
        insights = [
            f"Rated by only {rated_by} of 7 SEBI-registered agencies — significant coverage gap ACER can fill",
            f"Active issuer with {total} instruments — proven willingness to pay for ratings",
            f"Currently with {comps} — ACER can offer a competitive parallel or second opinion rating",
        ]
        watch_outs = [
            f"Existing relationship with {comps} — needs a strong value proposition to add ACER",
            "Multi-agency rating adds cost — pitch the investor diversification and pricing benefit",
        ]
        best_pitch = "NCD" if entity in ("NBFC", "Corporate") else "Bond"

    else:
        score = 52
        comps = ", ".join(active_agencies[:3]) if active_agencies else "multiple agencies"
        opportunity = f"Competitive entry — already rated by {rated_by} of 7 agencies"
        urgency = "Low"
        insights = [
            f"Broad coverage by {rated_by} agencies including {comps}",
            "Possible angle: pitch ACER for new instruments not yet rated by others",
            "Long-term relationship approach recommended — attend their investor/lender meets",
        ]
        watch_outs = [
            f"Already well-covered by {rated_by} agencies — pricing and TAT must be compelling",
            "Risk of over-pitched company — sales approach must be highly differentiated",
        ]
        best_pitch = "New instrument" if entity in ("NBFC", "Corporate") else "Subordinated Bond"

    # ── Recommended action ────────────────────────────────────────────────────
    if acer_is_rating:
        action = f"Schedule quarterly review with {name}'s CFO/Treasury team — track upcoming instrument maturities and new fundraising plans"
    elif rated_by == 0:
        action = f"Reach out to {name}'s CFO with ACER's first-time issuer package — highlight RBI/SEBI mandate benefits and cost savings vs bank debt"
    elif rated_by <= 2:
        action = f"Approach {name}'s treasury team with a competitive proposal — offer parallel rating with faster TAT than their existing agency"
    else:
        action = f"Place {name} on a 6-month watch list — approach when a new instrument is announced or existing ratings are up for renewal"

    return {
        "fit_score":                  score,
        "fit_label":                  _label(score),
        "opportunity_type":           opportunity,
        "key_insights":               insights,
        "watch_outs":                 watch_outs,
        "recommended_action":         action,
        "best_instrument_pitch":      best_pitch,
        "urgency":                    urgency,
        "already_rated_by_infomerics": acer_is_rating,
    }


async def analyze_fit(company: dict, credit_data: dict) -> dict:
    """
    Produce ACER fit analysis. Always generates a real data-driven result
    first, then enriches with AI if OpenRouter key is configured.
    """
    # Always compute the data-driven baseline first
    result = _data_driven_analysis(company, credit_data)

    # Try AI enrichment if key is available
    agencies       = credit_data.get("agencies", [])
    rating_summary = _build_rating_summary(agencies)
    rated_by_count = credit_data.get("rated_by_count", 0)

    prompt = FIT_PROMPT.format(
        name=company.get("name", ""),
        entity_type=company.get("entity_type", "Unknown"),
        address=company.get("address", "India"),
        cin=company.get("cin", "Not found"),
        incorporation_date=company.get("incorporation_date", "N/A"),
        instrument_count=credit_data.get("total_instruments", 0),
        rating_summary=rating_summary,
        rated_by_count=rated_by_count,
    )

    raw    = await chat(prompt, max_tokens=600)
    parsed = parse_json(raw) if raw else None

    if parsed and isinstance(parsed.get("fit_score"), (int, float)):
        parsed["fit_label"] = _label(int(parsed["fit_score"]))
        return parsed

    return result
