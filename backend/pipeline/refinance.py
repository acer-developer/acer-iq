"""
REFINANCE WINDOW - issuers with listed debt maturing soon.

ROADMAP_V3 phase 2: "maturing-NCD refinance window detector from the BSE scrip
master we already cache". The trigger is simple and genuinely predictive: a
company with an NCD maturing next year has to refinance it, refinancing needs a
rating, and that decision is made months ahead. Getting there before the
mandate is placed is the entire premise of the product.

It costs no new network call. `bse_scraper._load_master()` already holds every
active BSE debt scrip, cached 12 hours, because the company search needs it.

PRECISION, STATED HONESTLY. BSE's master carries no maturity date field. The
year is inferred from the scrip id's own convention ('10CAGL27' -> 10.00% due
2027) by `bse_scraper._from_scrip_id`, so this module resolves to a YEAR and
never to a date. Every row says so. A refinance window quoted to the month
would be invented precision, and instruments whose id does not follow the
convention - most CPs, odd ids - carry no year at all and are reported as
unknown rather than silently dropped.

This is a lead SIGNAL, not a credit view: it says an issuer will need a rating,
never that ACER should want them. The credit screen in winnability.py is what
decides that, exactly as it does for the queue.

Self-check (no network):  python -m backend.pipeline.refinance
"""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger("acer-iq.refinance")

# How far ahead counts as a refinance window. Two years because the decision and
# the arranger conversation both happen well before maturity - by the time a
# bond matures the replacement rating was mandated months earlier.
DEFAULT_HORIZON_YEARS = 2


def _year_of(inst: dict) -> int | None:
    """The maturity year an instrument resolves to, or None if its id does not
    follow BSE's convention."""
    raw = (inst.get("maturity_date") or "").strip()
    if not raw.isdigit() or len(raw) != 4:
        return None
    y = int(raw)
    # A year outside a sane bond horizon means the scrip id was misread, not
    # that someone issued 80-year paper.
    return y if 1990 <= y <= 2100 else None


def window_for(instruments: list[dict], this_year: int,
               horizon_years: int = DEFAULT_HORIZON_YEARS) -> dict:
    """Summarise one issuer's maturity profile against the refinance horizon."""
    years = [y for y in (_year_of(i) for i in instruments) if y is not None]
    unknown = len(instruments) - len(years)

    maturing = sorted(y for y in years if this_year <= y <= this_year + horizon_years)
    matured = [y for y in years if y < this_year]

    nearest = maturing[0] if maturing else None
    if nearest is None:
        urgency = "none"
    elif nearest <= this_year:
        urgency = "now"
    elif nearest == this_year + 1:
        urgency = "next_year"
    else:
        urgency = "horizon"

    return {
        "maturing_count": len(maturing),
        "maturing_years": maturing,
        "nearest_year": nearest,
        "years_until": (nearest - this_year) if nearest is not None else None,
        "urgency": urgency,
        "already_matured": len(matured),
        # Not decoration: if most of an issuer's scrips carry no readable year,
        # "no maturity in the window" means we could not tell, not that there
        # is none.
        "unknown_maturity": unknown,
        "instruments_total": len(instruments),
    }


def build_refinance_list(master: dict, horizon_years: int = DEFAULT_HORIZON_YEARS,
                         this_year: int | None = None,
                         parse=None) -> list[dict]:
    """Issuers with listed debt maturing inside the horizon, soonest first.

    `master` is `bse_scraper._load_master()`'s index: normalised issuer name ->
    raw scrip rows. `parse` defaults to `bse_scraper._parse_scrip`, injectable
    so the self-check needs neither network nor that module's internals.
    """
    if parse is None:
        from backend.pipeline.bse_scraper import _parse_scrip
        parse = _parse_scrip
    this_year = this_year or datetime.now().year

    rows = []
    for _key, raw_rows in master.items():
        instruments = [parse(r) for r in raw_rows]
        issuer = ""
        for r in raw_rows:
            issuer = (r.get("Issuer_Name") or r.get("Scrip_Name") or "").strip()
            if issuer:
                break
        if not issuer:
            continue

        w = window_for(instruments, this_year, horizon_years)
        if not w["maturing_count"]:
            continue

        coupons = [i["coupon_rate"] for i in instruments if i.get("coupon_rate")]
        rows.append({
            "company_name": issuer,
            "bse_scrip_code": next((i.get("bse_scrip_code") for i in instruments
                                    if i.get("bse_scrip_code")), ""),
            "coupons": sorted(set(coupons))[:5],
            **w,
        })

    rows.sort(key=lambda r: (r["nearest_year"], -r["maturing_count"], r["company_name"]))
    return rows


async def fetch_refinance_list(horizon_years: int = DEFAULT_HORIZON_YEARS) -> dict:
    """The refinance list off the cached BSE master. Never raises on a BSE
    outage - it reports an unreachable source, which is not the same fact as an
    empty list and must never render as one."""
    from backend.pipeline.bse_scraper import _bse_tripped, _load_master

    master = await _load_master()
    if not master:
        return {
            "issuers": [], "total": 0, "horizon_years": horizon_years,
            "data_status": "unverified",
            "note": ("BSE scrip master unreachable"
                     + (" (circuit breaker open)" if _bse_tripped() else "")
                     + " - this is not an empty result, it is no result."),
        }

    rows = build_refinance_list(master, horizon_years)
    year = datetime.now().year
    return {
        "issuers": rows,
        "total": len(rows),
        "horizon_years": horizon_years,
        "this_year": year,
        "data_status": "ok",
        "by_urgency": {
            "now": sum(1 for r in rows if r["urgency"] == "now"),
            "next_year": sum(1 for r in rows if r["urgency"] == "next_year"),
            "horizon": sum(1 for r in rows if r["urgency"] == "horizon"),
        },
        "note": (f"Listed BSE debt maturing {year}-{year + horizon_years}. "
                 "Maturity is resolved to a YEAR from the scrip id convention, "
                 "not to a date - BSE's master carries no maturity date field. "
                 "Instruments whose id does not follow that convention are "
                 "counted as unknown_maturity, not as 'no maturity'."),
    }


# -- self-check (no network) -------------------------------------------------

def _demo() -> None:
    def parse(row):
        """Stand-in for bse_scraper._parse_scrip - keeps this offline."""
        return {"maturity_date": row.get("y", ""), "coupon_rate": row.get("c", ""),
                "bse_scrip_code": row.get("code", "")}

    # -- window_for
    w = window_for([{"maturity_date": "2026"}, {"maturity_date": "2027"},
                    {"maturity_date": "2031"}, {"maturity_date": ""}], 2026, 2)
    assert w["maturing_count"] == 2 and w["maturing_years"] == [2026, 2027], w
    assert w["nearest_year"] == 2026 and w["urgency"] == "now", w
    assert w["unknown_maturity"] == 1, "a CP with an unreadable id is unknown, not absent"
    assert w["years_until"] == 0

    nxt = window_for([{"maturity_date": "2027"}], 2026, 2)
    assert nxt["urgency"] == "next_year" and nxt["years_until"] == 1, nxt
    far = window_for([{"maturity_date": "2028"}], 2026, 2)
    assert far["urgency"] == "horizon", far

    # Already-matured paper is not a refinance signal.
    past = window_for([{"maturity_date": "2019"}], 2026, 2)
    assert past["maturing_count"] == 0 and past["urgency"] == "none", past
    assert past["already_matured"] == 1

    # A misread scrip id must not become 80-year paper.
    assert _year_of({"maturity_date": "2205"}) is None
    assert _year_of({"maturity_date": "20"}) is None
    assert _year_of({"maturity_date": ""}) is None
    assert _year_of({"maturity_date": "2027"}) == 2027

    # -- build_refinance_list
    master = {
        "ALPHA": [{"Issuer_Name": "Alpha Ltd", "y": "2027", "c": "8.50", "code": "1"},
                  {"Issuer_Name": "Alpha Ltd", "y": "2027", "c": "8.50", "code": "1"}],
        "BETA":  [{"Issuer_Name": "Beta Ltd", "y": "2026", "c": "9.10", "code": "2"}],
        "GAMMA": [{"Issuer_Name": "Gamma Ltd", "y": "2035", "c": "7.00", "code": "3"}],
        "BLANK": [{"Issuer_Name": "", "y": "2026"}],
    }
    out = build_refinance_list(master, horizon_years=2, this_year=2026, parse=parse)
    names = [r["company_name"] for r in out]
    assert names == ["Beta Ltd", "Alpha Ltd"], names
    assert "Gamma Ltd" not in names, "2035 is outside a 2-year horizon"
    assert out[0]["urgency"] == "now" and out[1]["urgency"] == "next_year"
    assert out[1]["maturing_count"] == 2, out[1]
    assert out[1]["coupons"] == ["8.50"], out[1]
    # An issuer with no readable name cannot be called, so it is not a lead.
    assert len(out) == 2

    print("refinance self-check: ok")


if __name__ == "__main__":
    _demo()
