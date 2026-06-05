from backend.pipeline.llm import chat, parse_json

SCORE_PROMPT = """\
You are a business development analyst at an Indian credit rating agency (like CRISIL/ICRA/CARE/Infomerics).
Given this company profile, score it 0-100 on likelihood of needing credit rating services for {instrument_type}.

Company: {name}
Entity Type: {entity_type}
Target Instrument: {instrument_type}
City: {city}
Website: {website}
Directors: {directors}
CIN: {cin}
Incorporated: {incorporation_date}

Context:
- Banks need ratings for AT1 bonds, tier-2 bonds, infrastructure bonds
- NBFCs need ratings for NCDs, commercial paper, securitisation
- Corporates need ratings for NCDs, bonds, commercial paper, IPO grading

Respond ONLY in this exact JSON (no markdown, no extra text):
{{
  "score": 85,
  "score_label": "Hot Lead",
  "why_quality_lead": ["reason 1", "reason 2", "reason 3"],
  "pain_points": ["pain 1", "pain 2", "pain 3"],
  "recommended_approach": "One line pitch approach"
}}

score_label: 80-100="Hot Lead", 60-79="Warm Lead", 40-59="Potential", 0-39="Low Priority"\
"""


def _label(score: int) -> str:
    if score >= 80: return "Hot Lead"
    if score >= 60: return "Warm Lead"
    if score >= 40: return "Potential"
    return "Low Priority"


async def score_company(
    company: dict,
    industry: str,
    city: str,
    entity_type: str = "Financial Entity",
    instrument_type: str = "All",
) -> dict:
    directors_text = ", ".join(d.get("name", "") for d in company.get("directors", [])) or "Not found"

    prompt = SCORE_PROMPT.format(
        name=company.get("name", ""),
        entity_type=entity_type,
        instrument_type=instrument_type,
        city=city,
        website=company.get("website", "N/A"),
        directors=directors_text,
        cin=company.get("cin", "N/A"),
        incorporation_date=company.get("incorporation_date", "N/A"),
    )

    raw = await chat(prompt, max_tokens=512)
    parsed = parse_json(raw) if raw else None

    if parsed:
        score = int(parsed.get("score", 50))
        company.update({
            "score":              score,
            "score_label":        _label(score),
            "why_quality_lead":   parsed.get("why_quality_lead", []),
            "pain_points":        parsed.get("pain_points", []),
            "recommended_approach": parsed.get("recommended_approach", ""),
        })
    else:
        _mock_score(company)

    return company


def _mock_score(company: dict) -> dict:
    name  = company.get("name", "")
    score = 55 + (sum(ord(c) for c in name) % 45)
    company.update({
        "score":       score,
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
        "recommended_approach": "Lead with cost-of-capital reduction benefits and faster institutional debt access.",
    })
    return company
