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


def search(location: str, entity_type: str = "All", limit: int = 60) -> list[dict]:
    """
    location: city name or 6-digit pincode.
    entity_type: Banks | NBFCs | All  (Corporates are not in the registry yet).
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

    with _connect() as con:
        if is_pin:
            # Same sorting-district (first 3 digits) ≈ same city area
            rows = con.execute(
                f"""SELECT * FROM companies
                    WHERE {type_clause} AND pincode LIKE ?""",
                (loc[:3] + "%",),
            ).fetchall()
        else:
            rows = con.execute(
                f"""SELECT * FROM companies
                    WHERE {type_clause}
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


def stats() -> dict:
    if not available():
        return {"available": False}
    with _connect() as con:
        total = con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        by_type = dict(con.execute(
            "SELECT entity_type, COUNT(*) FROM companies GROUP BY entity_type"
        ).fetchall())
    return {"available": True, "total": total, "by_type": by_type}
