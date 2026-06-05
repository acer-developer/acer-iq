import json
import anthropic
from backend.config import settings


SCORE_PROMPT = """\
You are a business development analyst at an Indian credit rating agency (like CRISIL/ICRA/CARE/Infomerics).
Given this company profile, score it 0-100 on likelihood of needing credit rating services for {instrument_type}, \
and explain why in 3 bullet points. Also identify 3 pain points this company likely faces \
that a credit rating would solve.

Company: {name}
Entity Type: {entity_type}
Target Instrument: {instrument_type}
City: {city}
Website: {website}
Directors found: {directors}
CIN: {cin}
Incorporated: {incorporation_date}

Context:
- Banks typically need ratings for AT1 bonds, tier-2 bonds, infrastructure bonds
- NBFCs typically need ratings for NCDs, commercial paper, securitisation
- Corporates typically need ratings for NCDs, bonds, commercial paper, IPO grading

Respond in this EXACT JSON format (no markdown, no extra text):
{{
  "score": 85,
  "score_label": "Hot Lead",
  "why_quality_lead": ["reason 1", "reason 2", "reason 3"],
  "pain_points": ["pain 1", "pain 2", "pain 3"],
  "recommended_approach": "One line on how to pitch them"
}}

Score labels: 80-100 = Hot Lead, 60-79 = Warm Lead, 40-59 = Potential, 0-39 = Low Priority\
"""


def _label(score: int) -> str:
    if score >= 80:
        return "Hot Lead"
    if score >= 60:
        return "Warm Lead"
    if score >= 40:
        return "Potential"
    return "Low Priority"


async def score_company(company: dict, industry: str, city: str, entity_type: str = "Financial Entity", instrument_type: str = "All") -> dict:
    """Call Claude to score and enrich one company. Returns updated company dict."""
    api_key = settings.anthropic_api_key
    if not api_key or api_key == "your_key_here":
        return _mock_score(company)

    directors_text = ", ".join(
        d.get("name", "") for d in company.get("directors", [])
    ) or "Not found"

    prompt = SCORE_PROMPT.format(
        name=company.get("name", ""),
        industry=industry,
        city=city,
        entity_type=entity_type,
        instrument_type=instrument_type,
        website=company.get("website", "N/A"),
        directors=directors_text,
        cin=company.get("cin", "N/A"),
        incorporation_date=company.get("incorporation_date", "N/A"),
    )

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        parsed = json.loads(raw)
        score = int(parsed.get("score", 50))
        company.update({
            "score": score,
            "score_label": _label(score),
            "why_quality_lead": parsed.get("why_quality_lead", []),
            "pain_points": parsed.get("pain_points", []),
            "recommended_approach": parsed.get("recommended_approach", ""),
        })

    except Exception as exc:
        company.update({
            "score": 0,
            "score_label": "Pending",
            "why_quality_lead": [],
            "pain_points": [],
            "recommended_approach": f"AI scoring unavailable: {exc}",
        })

    return company


def _mock_score(company: dict) -> dict:
    """Deterministic mock scores for demo/testing without API key."""
    name = company.get("name", "")
    # Simple hash for deterministic variation
    score = 55 + (sum(ord(c) for c in name) % 45)
    company.update({
        "score": score,
        "score_label": _label(score),
        "why_quality_lead": [
            "Company operates in a capital-intensive sector requiring debt financing",
            "Size and structure indicate likely need for external credit validation",
            "Sector growth trends suggest upcoming fundraising activity",
        ],
        "pain_points": [
            "Difficulty accessing institutional debt without a formal credit rating",
            "Higher borrowing costs due to lack of rated instruments",
            "Limited investor base restricts growth capital options",
        ],
        "recommended_approach": (
            "Lead with cost-of-capital reduction benefits and faster institutional debt access."
        ),
    })
    return company
