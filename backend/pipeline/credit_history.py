import httpx
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


async def fetch_credit_history(company_name: str, cin: str = "", symbol: str = "") -> dict:
    """
    Build the 7-agency rating matrix from two sources:
      1. NSE corporate disclosures (SEBI-mandated rating-action filings) —
         primary; real agency names, ratings, actions, dates.
      2. BSE debt-search instruments — secondary, merged when reachable.

    Returns data_status so the UI can distinguish "verified: not rated"
    from "sources unreachable: unknown".
    """
    from backend.pipeline.nse_ratings import fetch_rating_actions

    nse_actions, nse_status = await fetch_rating_actions(company_name, symbol=symbol)
    instruments = await fetch_past_instruments(company_name)

    agency_map: dict[str, list[dict]] = {ag["key"]: [] for ag in AGENCIES}

    # NSE rating actions (newest first already)
    for act in nse_actions:
        key = _match_agency(act["agency"])
        if key:
            agency_map[key].append({
                "security_name": act["rating"] or "Rating action",
                "isin": act["isin"],
                "instrument_type": act["action"] or "Disclosure",
                "rating": act["rating"],
                "issue_date": act["date"],
                "maturity_date": act["date"],
                "coupon_rate": "",
                "status": act["action"] or "Disclosed",
                "amount_crores": "",
                "source": "NSE disclosure",
            })

    # BSE instruments (when its API cooperates)
    for inst in instruments:
        key = _match_agency(inst.get("rating_agency", ""))
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
                "source": "BSE",
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
    total = len(nse_actions) + len(instruments)

    # Honest status: "ok" = we have verified data; "none_found" = sources
    # answered but nothing matched; "unverified" = sources unreachable, so
    # absence of ratings means NOTHING.
    if total > 0:
        data_status = "ok"
    elif nse_status == "none":
        data_status = "none_found"
    else:
        data_status = "unverified"

    return {
        "agencies": result_agencies,
        "total_instruments": total,
        "rated_by_count": rated_count,
        "raw_instruments": instruments,
        "rating_actions": nse_actions[:40],
        "data_status": data_status,
        "sources": {"nse": nse_status, "bse": "ok" if instruments else "empty_or_blocked"},
    }
