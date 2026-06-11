"""
Read-side of the registry: fast SQLite queries that power lead discovery.
Returns dicts shaped like the discovery pipeline expects.
"""

import logging
import sqlite3
from pathlib import Path

from backend.registry.geo import city_coords, jitter

log = logging.getLogger("registry.store")

DB_PATH = Path(__file__).parent / "data" / "registry.sqlite"

# Relevance: bigger/regulated entities first
_LAYER_RANK = {"Top": 0, "Upper": 1, "Middle": 2, "Base": 3, "": 4}
_SUB_RANK = {"SFB": 0, "UCB-Scheduled": 1, "UCB": 2}


def available() -> bool:
    return DB_PATH.exists()


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _rank(row: sqlite3.Row) -> tuple:
    return (
        _SUB_RANK.get(row["sub_type"], 5) if row["entity_type"] == "Bank"
        else _LAYER_RANK.get(row["layer"], 4),
        0 if row["deposit_taking"] else 1,
        0 if row["email"] else 1,
        row["name"],
    )


# Size tiers built on real RBI classifications:
#   large = RBI Upper/Middle-layer NBFCs (≥ ₹1,000 cr assets), Scheduled UCBs,
#           Small Finance Banks, ARCs
#   small = Base-layer NBFCs (< ₹1,000 cr assets), non-scheduled UCBs
_SIZE_CLAUSE = {
    "large": "(layer IN ('Middle','Upper','Top') OR sub_type IN ('UCB-Scheduled','SFB','ARC'))",
    "small": "(layer = 'Base' OR sub_type = 'UCB')",
}


def search(location: str, entity_type: str = "All", limit: int = 60,
           size: str = "All") -> list[dict]:
    """
    location: city name or 6-digit pincode.
    entity_type: Banks | NBFCs | All  (Corporates are not in the registry yet).
    size: All | large | small  (see _SIZE_CLAUSE).
    """
    if not available():
        log.warning("registry.sqlite missing — run python -m backend.registry.ingest")
        return []

    loc = location.split(",")[0].strip()
    is_pin = loc.isdigit() and len(loc) == 6

    type_clause = {
        "Banks": "entity_type = 'Bank'",
        "NBFCs": "entity_type IN ('NBFC', 'ARC')",
        "All":   "entity_type IN ('Bank', 'NBFC', 'ARC')",
    }.get(entity_type)
    if type_clause is None:
        return []

    size_clause = _SIZE_CLAUSE.get((size or "All").lower(), "1=1")

    with _connect() as con:
        if is_pin:
            # Same sorting-district (first 3 digits) ≈ same city area
            rows = con.execute(
                f"""SELECT * FROM companies
                    WHERE {type_clause} AND {size_clause} AND pincode LIKE ?""",
                (loc[:3] + "%",),
            ).fetchall()
        else:
            rows = con.execute(
                f"""SELECT * FROM companies
                    WHERE {type_clause} AND {size_clause}
                      AND (city = ? COLLATE NOCASE
                           OR rbi_region = ? COLLATE NOCASE
                           OR address LIKE ? COLLATE NOCASE)""",
                (loc, loc, f"%{loc}%"),
            ).fetchall()

    rows = sorted(rows, key=_rank)[:limit]

    fallback = city_coords(loc)
    out: list[dict] = []
    for r in rows:
        lat, lng = r["lat"], r["lng"]
        if lat is None or lng is None:
            if fallback:
                lat, lng = jitter(fallback[0], fallback[1], r["id"])
            else:
                continue  # cannot place on map and no city centroid

        entity = "Bank" if r["entity_type"] == "Bank" else "NBFC"
        sub = r["sub_type"]
        badge = {"SFB": "Small Finance Bank", "UCB-Scheduled": "Scheduled UCB",
                 "UCB": "Co-operative Bank"}.get(sub, sub)

        out.append({
            "id": r["id"],
            "name": r["name"],
            "address": r["address"],
            "lat": lat, "lng": lng,
            "website": "", "phone": "",
            "entity_type": entity,
            "cin": r["cin"] or "",
            "registry_email": r["email"] or "",
            "registry_sub_type": badge,
            "registry_layer": r["layer"] or "",
            "deposit_taking": bool(r["deposit_taking"]),
            "discovery_source": "rbi_registry",
        })
    return out


def _badge(row: sqlite3.Row) -> str:
    sub = row["sub_type"]
    label = {"SFB": "Small Finance Bank", "UCB-Scheduled": "Scheduled UCB",
             "UCB": "Co-operative Bank"}.get(sub, sub)
    if row["layer"]:
        label = f"{label} · {row['layer']} layer" if label else f"{row['layer']} layer"
    return label


def _row_get(row: sqlite3.Row, key: str) -> str:
    try:
        return row[key] or ""
    except (IndexError, KeyError):
        return ""


def suggest(q: str, limit: int = 12) -> list[dict]:
    """Name autocomplete over the whole RBI registry. Instant, offline."""
    if not available() or len(q.strip()) < 2:
        return []
    needle = q.strip()
    with _connect() as con:
        rows = con.execute(
            """SELECT * FROM companies
               WHERE name LIKE ? COLLATE NOCASE OR symbol LIKE ? COLLATE NOCASE
               ORDER BY CASE WHEN symbol = ? COLLATE NOCASE THEN 0
                             WHEN name LIKE ? COLLATE NOCASE THEN 1
                             WHEN symbol LIKE ? COLLATE NOCASE THEN 2
                             ELSE 3 END,
                        length(name)
               LIMIT ?""",
            (f"%{needle}%", f"{needle}%", needle, f"{needle}%", f"{needle}%", limit),
        ).fetchall()
    return [{
        "name": r["name"],
        "cin": r["cin"] or "",
        "bse_code": "",
        "symbol": _row_get(r, "symbol"),
        "sector": " · ".join(x for x in (
            _badge(r),
            f"NSE: {_row_get(r, 'symbol')}" if _row_get(r, "symbol") else "",
            r["city"],
        ) if x),
        "source": "nse_listed" if r["entity_type"] == "Listed" else "rbi_registry",
    } for r in rows]


def get_by_name(name: str) -> dict | None:
    """Exact-ish registry lookup for Company Research."""
    if not available() or not name.strip():
        return None
    n = name.strip()
    with _connect() as con:
        row = con.execute(
            "SELECT * FROM companies WHERE name = ? COLLATE NOCASE", (n,)
        ).fetchone()
        if row is None:
            row = con.execute(
                """SELECT * FROM companies WHERE name LIKE ? COLLATE NOCASE
                   ORDER BY length(name) LIMIT 1""",
                (f"%{n}%",),
            ).fetchone()
        if row is None and len(n) == 21:  # CIN lookup
            row = con.execute(
                "SELECT * FROM companies WHERE cin = ? COLLATE NOCASE", (n,)
            ).fetchone()
    if row is None:
        return None
    etype = {"Listed": "Corporate"}.get(row["entity_type"], row["entity_type"])
    return {
        "name": row["name"], "cin": row["cin"] or "",
        "address": row["address"] or "", "city": row["city"] or "",
        "state": row["state"] or "", "email": row["email"] or "",
        "entity_type": etype,
        "sub_type": _badge(row), "layer": row["layer"] or "",
        "deposit_taking": bool(row["deposit_taking"]),
        "symbol": _row_get(row, "symbol"), "isin": _row_get(row, "isin"),
        "source": "NSE listed-company master" if row["entity_type"] == "Listed" else "RBI registry",
    }


def stats() -> dict:
    if not available():
        return {"available": False}
    with _connect() as con:
        total = con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        by_type = dict(con.execute(
            "SELECT entity_type, COUNT(*) FROM companies GROUP BY entity_type"
        ).fetchall())
    return {"available": True, "total": total, "by_type": by_type}
