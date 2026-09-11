"""
FUNDAMENTALS - real financials for sizing a lead, from NSE's own XBRL filings.

WHY NOT A COMMERCIAL API. Both free tiers were tested against live keys and both
fail for India, so this is settled by evidence rather than docs:

  Twelve Data (Basic)   /income_statement, /balance_sheet and /statistics all
                        return 403 "available exclusively with pro or ultra or
                        venture or enterprise". Even an NSE quote needs the paid
                        Grow plan. Only /symbol_search works.
  Alpha Vantage (free)  OVERVIEW and INCOME_STATEMENT return an empty object for
                        RELIANCE.BSE, MUTHOOTFIN.BSE and 500325.BSE, while IBM
                        returns full data. Their "global" coverage is price data;
                        fundamentals are US-only.

NSE publishes quarterly results as Ind-AS XBRL, free, no key, and this module
reuses the cookie-warmed client nse_ratings already maintains. The numbers are
the filed ones, which is better provenance than a vendor's copy.

WHAT THIS GIVES THE SCORER. `fit_analyzer` scores on entity type, CIN and
instrument count with no financials at all, so a Rs 5,000 cr borrower looks
identical to a Rs 50 cr one. Interest coverage (EBIT / finance costs) is the
single most useful credit metric available here, and EBIT is derivable even
though no filing states it: EBIT = profit before tax + finance costs.

LIMIT WORTH KNOWING. NSE's results feed is a date-window listing, not a
per-company search, so this builds an index over a window and looks companies up
in it. A company that did not file inside the window is simply absent - which is
NOT the same as having no financials, and is reported as such.

The window default is 730 days for a reason found the hard way: a 180-day window
indexed only 7 symbols and looked like the endpoint was broken or gated. It is
not - 2 years indexes 2,126 symbols and 5 years 2,231, so the recent end of the
feed is simply sparse. Do not "optimise" this back down to a few months.

Self-check (no network):  python -m backend.pipeline.fundamentals
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from backend.pipeline.nse_ratings import _get_client, _warmup
from backend.pipeline.ttl_cache import TTLCache

log = logging.getLogger("acer-iq.fundamentals")

NSE_RESULTS = "https://www.nseindia.com/api/corporates-financial-results"

# Results arrive quarterly, so an index is good for hours rather than minutes.
_index_cache = TTLCache(ttl=6 * 3600, maxsize=8)
_xbrl_cache = TTLCache(ttl=24 * 3600, maxsize=400)

# Ind-AS XBRL tags. Namespace prefixes vary between filers (in-bse-fin, in-capmkt
# and others), so the prefix is matched loosely and the local name exactly - a
# loose local name would match RevenueFromOperationsBeforeExceptionalItems and
# similar, and silently score the wrong figure.
_TAGS = {
    "revenue":       ["RevenueFromOperations", "Revenue", "TotalIncome"],
    "finance_costs": ["FinanceCosts"],
    "pbt":           ["ProfitBeforeTax"],
    "net_profit":    ["ProfitLossForPeriod",
                      "ProfitLossForPeriodFromContinuingOperations"],
}


def _extract(xml: str, local_names: list[str]) -> float | None:
    """First value for the first tag that appears, or None.

    Returns the LARGEST absolute value among that tag's occurrences: one filing
    carries both the quarter and the year-to-date figure, and for sizing a lead
    the fuller period is the more useful one."""
    for name in local_names:
        hits = re.findall(rf"<[\w.-]+:{name}\b[^>]*>\s*(-?[\d.]+)\s*<", xml)
        vals = []
        for h in hits:
            try:
                vals.append(float(h))
            except ValueError:
                continue
        if vals:
            return max(vals, key=abs)
    return None


def parse_xbrl(xml: str) -> dict:
    """Pure parse: an Ind-AS XBRL filing -> the figures we can use.

    Values are in rupees as filed; `_crore` converts for display. Anything the
    filing does not state stays None - never zero, because zero finance costs
    would compute as infinite interest coverage and flatter a bad borrower."""
    out = {k: _extract(xml, names) for k, names in _TAGS.items()}

    # EBIT is not a filed line item but is derivable, and it is the input the
    # only metric that matters here needs.
    if out["pbt"] is not None and out["finance_costs"] is not None:
        out["ebit"] = out["pbt"] + out["finance_costs"]
    else:
        out["ebit"] = None

    fc, ebit = out["finance_costs"], out["ebit"]
    out["interest_coverage"] = round(ebit / fc, 2) if (ebit is not None and fc) else None
    return out


def _crore(v: float | None) -> float | None:
    return round(v / 1e7, 2) if v is not None else None


def summarise(figures: dict) -> dict:
    """The display shape: crores, plus a plain sentence a salesperson can read."""
    ic = figures.get("interest_coverage")
    if ic is None:
        verdict = "Interest coverage unavailable from this filing"
    elif ic < 1:
        verdict = f"Interest coverage {ic}x - operating profit does not cover interest"
    elif ic < 2:
        verdict = f"Interest coverage {ic}x - thin"
    else:
        verdict = f"Interest coverage {ic}x"
    return {
        "revenue_crore": _crore(figures.get("revenue")),
        "finance_costs_crore": _crore(figures.get("finance_costs")),
        "pbt_crore": _crore(figures.get("pbt")),
        "ebit_crore": _crore(figures.get("ebit")),
        "net_profit_crore": _crore(figures.get("net_profit")),
        "interest_coverage": ic,
        "verdict": verdict,
    }


_FILED_RE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})")
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def _filed_at(rec: dict) -> datetime:
    """NSE's 'filingDate' ("24-Aug-2026 17:39") as a datetime.

    An unparseable date sorts oldest, so a malformed record can never displace a
    good one as the latest filing."""
    m = _FILED_RE.search(rec.get("filingDate") or "")
    if not m:
        return datetime.min
    day, mon, year = m.groups()
    month = _MONTHS.get(mon.title())
    if not month:
        return datetime.min
    return datetime(int(year), month, int(day))


async def _results_index(days: int = 730) -> dict:
    """symbol -> newest filing record, over a trailing window."""
    key = f"idx:{days}"
    hit = _index_cache.get(key)
    if hit is not None:
        return hit

    client = _get_client()
    await _warmup(client)
    to_d = datetime.now()
    from_d = to_d - timedelta(days=days)
    r = await client.get(NSE_RESULTS, params={
        "index": "equities",
        "from_date": from_d.strftime("%d-%m-%Y"),
        "to_date": to_d.strftime("%d-%m-%Y"),
        "period": "Quarterly",
    })
    if r.status_code != 200:
        log.warning("NSE results listing HTTP %s - fundamentals unavailable",
                    r.status_code)
        return {}

    index: dict[str, dict] = {}
    for rec in r.json() or []:
        sym = (rec.get("symbol") or "").strip().upper()
        if not sym or not rec.get("xbrl"):
            continue
        # Keep the newest filing per symbol; the feed is not ordered. Compared as
        # a real datetime, not as text: "24-Aug-2026" sorts before "24-Dec-2025"
        # lexically, which would pin a stale filing as the latest one.
        prev = index.get(sym)
        if prev is None or _filed_at(rec) > _filed_at(prev):
            index[sym] = rec
    _index_cache.set(key, index)
    return index


async def fetch_for_symbol(symbol: str, days: int = 730) -> dict:
    """Financials for one NSE symbol.

    `status` distinguishes the three outcomes that must never be conflated:
      "ok"          figures parsed
      "not_filed"   the company did not file inside the window
      "unverified"  NSE could not be reached, so absence proves nothing
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return {"status": "not_filed", "symbol": symbol}

    cached = _xbrl_cache.get(sym)
    if cached is not None:
        return cached

    try:
        index = await _results_index(days)
    except Exception as e:
        log.warning("fundamentals index failed: %s: %s", type(e).__name__, e)
        return {"status": "unverified", "symbol": sym}

    if not index:
        return {"status": "unverified", "symbol": sym}

    rec = index.get(sym)
    if not rec:
        out = {"status": "not_filed", "symbol": sym,
               "detail": f"no quarterly filing on NSE in the last {days} days"}
        _xbrl_cache.set(sym, out)
        return out

    try:
        client = _get_client()
        x = await client.get(rec["xbrl"])
        if x.status_code != 200:
            return {"status": "unverified", "symbol": sym}
        figures = parse_xbrl(x.text)
    except Exception as e:
        log.warning("XBRL fetch/parse failed for %s: %s: %s", sym, type(e).__name__, e)
        return {"status": "unverified", "symbol": sym}

    out = {
        "status": "ok",
        "symbol": sym,
        "company_name": rec.get("companyName", ""),
        "period": rec.get("relatingTo", ""),
        "financial_year": rec.get("financialYear", ""),
        "consolidated": rec.get("consolidated", ""),
        "filing_date": rec.get("filingDate", ""),
        "source_url": rec.get("xbrl", ""),
        **summarise(figures),
    }
    _xbrl_cache.set(sym, out)
    return out


# -- self-check (no network) -------------------------------------------------

_FIXTURE = """<?xml version="1.0"?>
<xbrl xmlns:in-bse-fin="http://x">
  <in-bse-fin:RevenueFromOperations contextRef="Q">499725000.00</in-bse-fin:RevenueFromOperations>
  <in-bse-fin:RevenueFromOperations contextRef="YTD">1314142000.00</in-bse-fin:RevenueFromOperations>
  <in-bse-fin:FinanceCosts contextRef="Q">337313000.00</in-bse-fin:FinanceCosts>
  <in-bse-fin:ProfitBeforeTax contextRef="Q">-401562000.00</in-bse-fin:ProfitBeforeTax>
  <in-bse-fin:ProfitLossForPeriod contextRef="Q">-392075000.00</in-bse-fin:ProfitLossForPeriod>
</xbrl>"""


def _demo() -> None:
    f = parse_xbrl(_FIXTURE)

    # The fuller period wins: a filing carries both the quarter and the YTD.
    assert f["revenue"] == 1314142000.0, f

    # EBIT is derived, not filed: PBT + finance costs.
    assert f["ebit"] == -401562000.0 + 337313000.0, f
    assert f["interest_coverage"] == round(f["ebit"] / f["finance_costs"], 2)
    assert f["interest_coverage"] < 0, "a loss-making issuer must not look covered"

    s = summarise(f)
    assert s["revenue_crore"] == 131.41, s
    assert "does not cover interest" in s["verdict"], s

    # Nothing may be invented. A filing with no finance costs must not produce
    # infinite coverage, and a missing figure stays None rather than zero.
    bare = parse_xbrl("<xbrl></xbrl>")
    assert bare["revenue"] is None and bare["finance_costs"] is None
    assert bare["ebit"] is None and bare["interest_coverage"] is None
    assert summarise(bare)["revenue_crore"] is None
    assert "unavailable" in summarise(bare)["verdict"]

    zero_fc = parse_xbrl(
        '<xbrl xmlns:a="x"><a:FinanceCosts>0</a:FinanceCosts>'
        '<a:ProfitBeforeTax>100</a:ProfitBeforeTax></xbrl>')
    assert zero_fc["interest_coverage"] is None, "zero finance costs is not infinite cover"

    # A healthy issuer reads as healthy.
    good = parse_xbrl(
        '<xbrl xmlns:a="x"><a:FinanceCosts>100</a:FinanceCosts>'
        '<a:ProfitBeforeTax>400</a:ProfitBeforeTax></xbrl>')
    assert good["interest_coverage"] == 5.0, good
    assert summarise(good)["verdict"] == "Interest coverage 5.0x"

    # Filing dates must compare chronologically, not as text: "24-Aug-2026"
    # sorts before "24-Dec-2025" lexically, which would pin a stale filing.
    aug26 = _filed_at({"filingDate": "24-Aug-2026 17:39"})
    dec25 = _filed_at({"filingDate": "24-Dec-2025 09:00"})
    assert aug26 > dec25, (aug26, dec25)
    assert "24-Aug-2026" < "24-Dec-2025", "the text comparison this guards against"
    assert _filed_at({"filingDate": "garbage"}) == datetime.min
    assert _filed_at({}) == datetime.min

    print("fundamentals self-check: ok")


if __name__ == "__main__":
    _demo()
