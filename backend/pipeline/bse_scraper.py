import httpx

BSE_DEBT_SEARCH = "https://api.bseindia.com/BseIndiaAPI/api/GetDebtScripsSearchData/w"
BSE_DEBT_ALT = "https://api.bseindia.com/BseIndiaAPI/api/SearchData/w"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.bseindia.com",
}


def _parse_bse_item(item: dict) -> dict:
    return {
        "isin": item.get("ISIN_NO", item.get("ISIN", "")),
        "security_name": item.get("SCRIP_NAME", item.get("SECURITY_NAME", item.get("SecurityName", ""))),
        "instrument_type": item.get("CATEGORY", item.get("Category", item.get("INSTRUMENT", "NCD/Bond"))),
        "face_value": str(item.get("FACE_VALUE", item.get("FaceValue", ""))),
        "issue_date": item.get("ISSUE_DATE", item.get("IssueDate", "")),
        "maturity_date": item.get("MATURITY_DATE", item.get("REDEMPTION_DATE", item.get("MaturityDate", ""))),
        "coupon_rate": str(item.get("COUPON_RATE", item.get("CouponRate", ""))),
        "credit_rating": item.get("CREDIT_RATING", item.get("CreditRating", "")),
        "rating_agency": item.get("RATING_AGENCY", item.get("RatingAgency", "")),
        "status": item.get("STATUS", item.get("Status", "Listed")),
        "amount_crores": str(item.get("ISSUE_SIZE", item.get("IssueSize", item.get("Amount", "")))),
    }


async def fetch_past_instruments(company_name: str) -> list[dict]:
    """Fetch past NCD/Bond/Debt issuances for a company from BSE."""
    instruments = []

    try:
        async with httpx.AsyncClient(timeout=8, headers=_HEADERS) as client:
            resp = await client.get(
                BSE_DEBT_SEARCH,
                params={
                    "scripname": company_name,
                    "category": "",
                    "subcateg": "",
                    "status": "",
                    "exDate": "",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                rows = data.get("Table", data.get("data", []))
                if isinstance(rows, list):
                    for item in rows[:12]:
                        instruments.append(_parse_bse_item(item))
    except Exception:
        pass

    if not instruments:
        try:
            async with httpx.AsyncClient(timeout=8, headers=_HEADERS) as client:
                resp = await client.get(
                    BSE_DEBT_ALT,
                    params={
                        "strText": company_name,
                        "flag": "0",
                        "Membertype": "S",
                        "pageno": "1",
                        "tab": "DEBT",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    rows = data.get("Table", data.get("data", []))
                    if isinstance(rows, list):
                        for item in rows[:12]:
                            instruments.append(_parse_bse_item(item))
        except Exception:
            pass

    return instruments
