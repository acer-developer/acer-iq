"""
MATURING-NCD REFINANCE WINDOW - a forward-looking BD trigger.

Every other signal in this pipeline (cra_press, credit_history) only shows what
already happened this week. A bond maturing in 3-9 months is a dated,
predictable event: the issuer has to refinance, and refinancing needs a
rating. That is a lead with a known deadline, not a reaction to one.

Built entirely on the BSE active-scrip master `bse_scraper.py` already fetches
and caches for 12h (`_load_master`) - no second BSE call. This module only
reads that cache and regroups it; it owns no network I/O of its own.

KNOWN DATA LIMIT (see TODO.md "Known gaps"): BSE's scrip master carries no
issue date or issue size, and coupon/maturity are parsed out of BSE's own
scrip-id convention (`bse_scraper._from_scrip_id`), which only ever resolves to
a YEAR - never a month or day. Guessing a month would create a phantom
deadline and send BD chasing nothing, so a row with no maturity year at all is
dropped, never defaulted. A year-only maturity is treated as the full calendar
year it could fall in (Jan 1 - Dec 31) when checking the window; this is
conservative in the sense of including more, not less, and the row is scored
on the earliest date that year could mean.
ponytail: year-granularity only, so the "just inside the window" boundary is
fuzzy for live data (exact for tests, which pass full dates). Upgrade path is
BSE's per-scrip debt-detail endpoint if exact dates ever matter - same note
already left in bse_scraper.py.

Self-check (no network):  python -m backend.pipeline.refinance
"""
from __future__ import annotations

import calendar
import logging
import re
from datetime import date, timedelta

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


def _parse_maturity_range(mdate: str) -> tuple[date, date] | None:
    """A maturity string of unknown precision ("YYYY", "YYYY-MM", or
    "YYYY-MM-DD") into the [earliest, latest] date it could mean. Returns None
    for missing/unparseable input - callers must exclude, never guess."""
    mdate = (mdate or "").strip()
    m = _DATE_RE.match(mdate)
    if not m:
        return None
    y, mo, d = m.groups()
    y = int(y)
    if mo and d:
        dt = date(y, int(mo), int(d))
        return dt, dt
    if mo:
        mo = int(mo)
        last_day = calendar.monthrange(y, mo)[1]
        return date(y, mo, 1), date(y, mo, last_day)
    return date(y, 1, 1), date(y, 12, 31)


def _add_months(d: date, months: int) -> date:
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def maturing_within(scrips: list[dict], months_ahead: int = 9,
                     min_months: int = 3) -> list[dict]:
    """Pure function: BSE-shaped scrip dicts -> one row per issuer whose debt
    matures in [today + min_months, today + months_ahead].

    Each scrip dict is expected to carry `issuer_name` and `maturity_date`
    (year, "YYYY-MM", or full ISO date), plus whatever `bse_scraper._parse_scrip`
    produces (isin, security_name, coupon_rate, amount_crores, ...).
    """
    from backend.pipeline.lead_queue import _norm  # reuse the same issuer grouping

    today = date.today()
    lo = _add_months(today, min_months)
    hi = _add_months(today, months_ahead)

    grouped: dict[str, list[tuple[dict, date]]] = {}
    for s in scrips:
        rng = _parse_maturity_range(s.get("maturity_date", ""))
        if rng is None:
            continue  # missing/unparseable maturity date -> exclude, never guess
        earliest, latest = rng
        if latest < today:
            continue  # already matured
        if not (earliest <= hi and latest >= lo):
            continue  # window doesn't overlap [lo, hi]
        issuer_raw = (s.get("issuer_name") or "").strip()
        key = _norm(issuer_raw)
        if not key:
            continue
        grouped.setdefault(key, []).append((s, earliest, latest))

    rows = []
    for key, items in grouped.items():
        items.sort(key=lambda t: t[1])
        display_name = max((s.get("issuer_name", "") for s, _, _ in items), key=len)
        amounts = [a for s, _, _ in items if (a := _num(s.get("amount_crores"))) is not None]
        earliest, latest = items[0][1], items[0][2]

        # BSE's scrip-id convention yields a maturity YEAR for almost every live
        # row, never a day. Reporting that as "2026-01-01" reads as a precise
        # deadline - and one already in the past - which is exactly the phantom
        # deadline that sends BD chasing nothing. So the row carries its own
        # precision and a label safe to put in front of a salesperson.
        precision = ("day" if earliest == latest
                     else "month" if earliest.month == latest.month else "year")
        label = {
            "day":   earliest.isoformat(),
            "month": earliest.strftime("during %b %Y"),
            "year":  earliest.strftime("during %Y"),
        }[precision]

        rows.append({
            "issuer_name": display_name or key,
            "instruments": [s for s, _, _ in items],
            "instrument_count": len(items),
            "nearest_maturity": earliest.isoformat(),
            "maturity_latest": latest.isoformat(),
            "maturity_precision": precision,
            "maturity_label": label,
            "total_amount_crores": round(sum(amounts), 2) if amounts else None,
        })

    rows.sort(key=lambda r: r["nearest_maturity"])
    return rows


async def find_refinance_candidates(months_ahead: int = 9) -> dict:
    """Refinance candidates from the cached BSE scrip master.

    data_status follows the same vocabulary as cra_press.py / credit_history.py:
    "ok" (candidates found), "none_found" (master reachable, nothing in the
    window), "unverified" (BSE could not be reached at all - absence proves
    nothing).
    """
    from backend.pipeline import bse_scraper

    index = await bse_scraper._load_master()
    if not index:
        return {"candidates": [], "window_months": months_ahead,
                "data_status": "unverified"}

    scrips = []
    for rows in index.values():
        for row in rows:
            parsed = bse_scraper._parse_scrip(row)
            parsed["issuer_name"] = (row.get("Issuer_Name")
                                      or row.get("Scrip_Name") or "").strip()
            scrips.append(parsed)

    candidates = maturing_within(scrips, months_ahead=months_ahead)
    return {
        "candidates": candidates,
        "window_months": months_ahead,
        "data_status": "ok" if candidates else "none_found",
    }


# ── self-check (no network) ─────────────────────────────────────────────────

def _demo() -> None:
    today = date.today()
    hi = _add_months(today, 9)   # far edge of the default window
    lo = _add_months(today, 3)   # near edge of the default window

    scrips = [
        # Exactly on the far edge - just inside the window.
        {"issuer_name": "Spectron Engineers Limited",
         "maturity_date": hi.isoformat(),
         "isin": "INE001", "amount_crores": "100"},
        # Same issuer, name variant with a different legal suffix - must
        # collapse into the same row.
        {"issuer_name": "SPECTRON ENGINEERS PVT LTD",
         "maturity_date": (hi - timedelta(days=30)).isoformat(),
         "isin": "INE002", "amount_crores": "50"},
        # One day past the far edge - just outside the window.
        {"issuer_name": "Spectron Engineers Limited",
         "maturity_date": (hi + timedelta(days=1)).isoformat(),
         "isin": "INE003", "amount_crores": "200"},
        # Exactly on the near edge - just inside the window.
        {"issuer_name": "Atithi Paper Ltd", "maturity_date": lo.isoformat(),
         "isin": "INE010", "amount_crores": ""},
        # One day before the near edge - too soon, just outside the window.
        {"issuer_name": "Atithi Paper Ltd",
         "maturity_date": (lo - timedelta(days=1)).isoformat(),
         "isin": "INE011", "amount_crores": ""},
        # Missing maturity date entirely - must be excluded, not defaulted.
        {"issuer_name": "Steady Corp Limited", "maturity_date": "",
         "isin": "INE020", "amount_crores": "40"},
        # Already matured - must be excluded even though it's a real date.
        {"issuer_name": "Steady Corp Limited",
         "maturity_date": (today - timedelta(days=30)).isoformat(),
         "isin": "INE021", "amount_crores": "40"},
    ]

    rows = maturing_within(scrips, months_ahead=9, min_months=3)
    by_name = {r["issuer_name"].upper(): r for r in rows}

    # Name variants of one issuer collapse into a single row with both
    # in-window instruments, and the out-of-window one dropped.
    spectron = next(r for r in rows if "SPECTRON" in r["issuer_name"].upper())
    assert spectron["instrument_count"] == 2, spectron
    assert spectron["total_amount_crores"] == 150.0, spectron
    assert spectron["nearest_maturity"] == (hi - timedelta(days=30)).isoformat(), spectron

    # Boundary exactly at the far edge is included.
    assert any(i["isin"] == "INE001" for i in spectron["instruments"]), spectron
    # One day past the far edge is excluded.
    assert not any(i["isin"] == "INE003" for i in spectron["instruments"]), spectron

    # Boundary exactly at the near edge is included; one day short is not.
    atithi = by_name["ATITHI PAPER LTD"]
    assert atithi["instrument_count"] == 1, atithi
    assert atithi["instruments"][0]["isin"] == "INE010", atithi
    # Missing amount on the only in-window instrument -> no fabricated total.
    assert atithi["total_amount_crores"] is None, atithi

    # Steady Corp has no instrument left standing (missing date + matured) -
    # it must not appear at all.
    assert "STEADY CORP" not in by_name, rows

    print("refinance self-check: ok")


if __name__ == "__main__":
    _demo()
