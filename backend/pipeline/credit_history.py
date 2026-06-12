from backend.netutil import SourceStatus
from backend.pipeline.bse_scraper import fetch_past_instruments

AGENCIES = [
    {
        "key": "CRISIL",
        "name": "CRISIL",
        "full_name": "CRISIL Ratings",
        "search_url": "https://www.crisil.com/en/home/our-businesses/ratings/credit-rating-list.html",
        "aliases": ["CRISIL"],
    },
    {
        "key": "ICRA",
        "name": "ICRA",
        "full_name": "ICRA Limited",
        "search_url": "https://www.icra.in/Ratingsearch/result",
        "aliases": ["ICRA"],
    },
    {
        "key": "CARE",
        "name": "CARE Ratings",
        "full_name": "CARE Ratings Limited",
        "search_url": "https://www.careratings.com/rating-announcements.aspx",
        "aliases": ["CARE"],
    },
    {
        "key": "INDRA",
        "name": "India Ratings",
        "full_name": "India Ratings and Research",
        "search_url": "https://www.indiaratings.co.in/press-release",
        "aliases": ["INDIA RATINGS", "IND-RA", "FITCH"],
    },
    {
        "key": "ACUITE",
        "name": "Acuité",
        "full_name": "Acuité Ratings & Research",
        "search_url": "https://www.acuite.in/rating-search",
        "aliases": ["ACUIT", "SMERA"],
    },
    {
        "key": "BRICKWORK",
        "name": "Brickwork",
        "full_name": "Brickwork Ratings",
        "search_url": "https://www.brickworkratings.com/Rating-Search.aspx",
        "aliases": ["BRICKWORK", "BWR"],
    },
    {
        "key": "INFOMERICS",
        "name": "Infomerics",
        "full_name": "Infomerics Valuation and Rating",
        "search_url": "https://www.infomerics.com/rating-action",
        "aliases": ["INFOMERICS", "IVR"],
    },
]


def _match_agency(agency_str: str) -> str | None:
    if not agency_str:
        return None
    upper = agency_str.upper()
    for ag in AGENCIES:
        for alias in ag["aliases"]:
            if alias in upper:
                return ag["key"]
    return None


def _latest_rating(instruments: list[dict]) -> str:
    for inst in instruments:
        r = inst.get("rating", "")
        if r:
            return r
    return "—"


async def fetch_credit_history(company_name: str, cin: str = "",
                               status: SourceStatus | None = None) -> dict:
    """
    Fetch all debt instruments for a company from BSE and map them
    across the 7 credit rating agencies.
    """
    instruments = await fetch_past_instruments(company_name, status=status)

    agency_map: dict[str, list[dict]] = {ag["key"]: [] for ag in AGENCIES}

    for inst in instruments:
        raw_agency = inst.get("rating_agency", "")
        key = _match_agency(raw_agency)
        if key:
            agency_map[key].append({
                "security_name": inst.get("security_name", ""),
                "isin": inst.get("isin", ""),
                "instrument_type": inst.get("instrument_type", ""),
                "rating": inst.get("credit_rating", ""),
                "issue_date": inst.get("issue_date", ""),
                "maturity_date": inst.get("maturity_date", ""),
                "coupon_rate": inst.get("coupon_rate", ""),
                "status": inst.get("status", ""),
                "amount_crores": inst.get("amount_crores", ""),
            })

    result_agencies = []
    for ag in AGENCIES:
        key = ag["key"]
        insts = agency_map[key]
        result_agencies.append({
            "key": key,
            "name": ag["name"],
            "full_name": ag["full_name"],
            "search_url": ag["search_url"],
            "total_instruments": len(insts),
            "latest_rating": _latest_rating(insts),
            "instruments": insts,
            "is_rated": len(insts) > 0,
        })

    rated_count = sum(1 for a in result_agencies if a["is_rated"])

    return {
        "agencies": result_agencies,
        "total_instruments": len(instruments),
        "rated_by_count": rated_count,
        "raw_instruments": instruments,
    }
