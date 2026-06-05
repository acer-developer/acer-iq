import json
import anthropic
from backend.config import settings

FIT_PROMPT = """\
You are a strategic business analyst at Infomerics Valuation and Rating Pvt. Ltd. (also called ACER), \
an Indian SEBI-registered credit rating agency.

Evaluate whether the following company is a strong target for Infomerics to pitch credit rating services.

Company: {name}
Entity Type: {entity_type}
Location: {address}
CIN: {cin}
Total BSE-listed instruments found: {instrument_count}

Current credit rating coverage (from BSE data):
{rating_summary}

Number of agencies currently rating this company: {rated_by_count} out of 7

Evaluate from Infomerics' sales perspective:
- Is there a clear mandate opportunity?
- Is Infomerics already rating them?
- What specific instrument should Infomerics pitch?
- What is the urgency?

Respond ONLY in this exact JSON format (no markdown, no extra text):
{{
  "fit_score": 78,
  "fit_label": "Good Fit",
  "opportunity_type": "New NCD mandate",
  "key_insights": [
    "Currently unrated by Infomerics — clear first-mover opportunity",
    "Active NCD issuances indicate ongoing rating needs",
    "NBFC sector aligns with Infomerics' core strength"
  ],
  "watch_outs": [
    "Already rated by CRISIL — competitive pitch required"
  ],
  "recommended_action": "Approach CFO with competitive NCD rating proposal highlighting turnaround time",
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
            lines.append(f"  - {ag['full_name']}: {ag['latest_rating']} ({ag['total_instruments']} instrument(s))")
        else:
            lines.append(f"  - {ag['full_name']}: Not rated / no BSE data")
    return "\n".join(lines)


def _label(score: int) -> str:
    if score >= 80:
        return "Strong Fit"
    if score >= 60:
        return "Good Fit"
    if score >= 40:
        return "Moderate Fit"
    return "Low Priority"


async def analyze_fit(company: dict, credit_data: dict) -> dict:
    """Call Claude to produce a fit analysis for the given company."""
    api_key = settings.anthropic_api_key
    if not api_key or api_key == "your_key_here":
        return _mock_analysis(company, credit_data)

    agencies = credit_data.get("agencies", [])
    rating_summary = _build_rating_summary(agencies)
    rated_by_count = credit_data.get("rated_by_count", 0)

    prompt = FIT_PROMPT.format(
        name=company.get("name", ""),
        entity_type=company.get("entity_type", "Unknown"),
        address=company.get("address", "India"),
        cin=company.get("cin", "Not found"),
        instrument_count=credit_data.get("total_instruments", 0),
        rating_summary=rating_summary,
        rated_by_count=rated_by_count,
    )

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        parsed["fit_label"] = _label(int(parsed.get("fit_score", 50)))
        return parsed
    except Exception as e:
        return _mock_analysis(company, credit_data)


def _mock_analysis(company: dict, credit_data: dict) -> dict:
    rated_by = credit_data.get("rated_by_count", 0)
    total = credit_data.get("total_instruments", 0)
    entity = company.get("entity_type", "Corporate")

    if rated_by == 0:
        score = 85
        opportunity = "New mandate — company has no BSE-listed rated instruments"
        insights = [
            "Currently unrated on BSE — Infomerics can be the first mover",
            f"{entity} entities in this segment are high-priority targets for new mandates",
            "No competitive displacement needed — clean slate opportunity",
        ]
        urgency = "High"
    elif rated_by <= 2:
        score = 72
        opportunity = f"Secondary mandate — rated by {rated_by} agency(ies), room for Infomerics"
        insights = [
            f"Rated by only {rated_by} of 7 agencies — significant whitespace",
            "Companies often prefer multiple rating opinions for large issuances",
            "Competitive pitch with faster TAT and sector expertise recommended",
        ]
        urgency = "Medium"
    else:
        score = 52
        opportunity = f"Competitive entry — already rated by {rated_by} agencies"
        insights = [
            f"Broad coverage by {rated_by} agencies — needs strong differentiation",
            "Focus pitch on specific upcoming instrument not yet rated",
            "Long-term relationship building recommended over transaction pitch",
        ]
        urgency = "Low"

    infomerics_rated = any(
        a["key"] == "INFOMERICS" and a["is_rated"]
        for a in credit_data.get("agencies", [])
    )

    return {
        "fit_score": score,
        "fit_label": _label(score),
        "opportunity_type": opportunity,
        "key_insights": insights,
        "watch_outs": [
            "Verify latest rating status before outreach — BSE data may lag",
            "Confirm key decision-maker (CFO / Treasury Head) before pitching",
        ],
        "recommended_action": f"Reach out to {company.get('name', 'company')} treasury team with a tailored {entity.lower()} rating proposal",
        "best_instrument_pitch": "NCD" if entity in ("NBFC", "Corporate") else "Bond",
        "urgency": urgency,
        "already_rated_by_infomerics": infomerics_rated,
    }
