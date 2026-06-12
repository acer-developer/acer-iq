"""
Registry tests against the COMMITTED RBI source files and registry.sqlite —
the real recorded fixtures for ingestion. Parsing the XLSX/PDF needs the
ingest extras (openpyxl, pdfplumber); those tests skip if they're missing.
"""

import pytest

from backend.registry import store
from backend.registry.geo import (
    city_coords, extract_city, extract_pin, jitter, state_from_cin, state_from_pin,
)
from backend.registry.ingest import DATA_DIR, parse_nbfc_xlsx, parse_ucb_pdf, sfb_rows


# ── Source-file parsers ───────────────────────────────────────────────────────

def test_parse_nbfc_xlsx_full_universe():
    openpyxl = pytest.importorskip("openpyxl")  # noqa: F841
    path = DATA_DIR / "rbi_nbfc.xlsx"
    if not path.exists():
        pytest.skip("rbi_nbfc.xlsx not committed")

    rows = parse_nbfc_xlsx(path)
    nbfcs = [r for r in rows if r["entity_type"] == "NBFC"]
    arcs = [r for r in rows if r["entity_type"] == "ARC"]
    assert len(nbfcs) > 9000, "expected the complete RBI NBFC universe"
    assert len(arcs) >= 25

    sample = nbfcs[0]
    for field in ("id", "name", "sub_type", "cin", "address", "city",
                  "state", "pincode", "email", "rbi_region", "source"):
        assert field in sample

    # the NBFC email column is what becomes a lead contact — must survive parsing
    with_email = sum(1 for r in nbfcs if "@" in r["email"])
    assert with_email > 8000

    # state resolution from CIN/pincode works for the vast majority
    with_state = sum(1 for r in rows if r["state"])
    assert with_state / len(rows) > 0.9


def test_parse_ucb_pdf_head_offices():
    try:
        import pdfplumber  # noqa: F401
    except Exception as exc:  # broken native deps raise more than ImportError
        pytest.skip(f"pdfplumber unavailable: {exc!r}")
    path = DATA_DIR / "ucb_scheduled.pdf"
    if not path.exists():
        pytest.skip("ucb_scheduled.pdf not committed")

    rows = parse_ucb_pdf(path, scheduled=True)
    assert len(rows) > 40  # scheduled UCB list
    assert all(r["entity_type"] == "Bank" for r in rows)
    assert all(r["sub_type"] == "UCB-Scheduled" for r in rows)
    # pincode column is the head-office pin
    with_pin = sum(1 for r in rows if r["pincode"].isdigit() and len(r["pincode"]) == 6)
    assert with_pin / len(rows) > 0.8


def test_sfb_rows():
    rows = sfb_rows()
    assert len(rows) == 11
    assert all(r["sub_type"] == "SFB" for r in rows)
    assert any(r["city"] == "Jaipur" for r in rows)  # AU SFB


# ── Query layer (committed registry.sqlite) ───────────────────────────────────

@pytest.fixture(autouse=True)
def _needs_registry():
    if not store.available():
        pytest.skip("registry.sqlite not built — run python -m backend.registry.ingest")


def test_search_banks_in_jaipur():
    results = store.search("Jaipur", "Banks")
    assert results, "Jaipur must return cooperative bank head offices"
    assert all(r["entity_type"] == "Bank" for r in results)
    assert all(r["discovery_source"] == "rbi_registry" for r in results)
    assert all(isinstance(r["lat"], float) and isinstance(r["lng"], float) for r in results)
    names = " ".join(r["name"].lower() for r in results)
    assert "bank" in names
    # AU Small Finance Bank is headquartered in Jaipur and must rank near the top
    assert any("au small finance" in r["name"].lower() for r in results[:5])


def test_search_nbfcs_in_jaipur_have_cin_and_email_contact():
    results = store.search("Jaipur", "NBFCs")
    assert len(results) >= 30
    assert all(r["entity_type"] == "NBFC" for r in results)
    assert any(r["cin"].startswith(("U", "L")) for r in results)
    assert any("@" in r["registry_email"] for r in results)


def test_search_by_pincode():
    results = store.search("302001", "All")  # Jaipur GPO
    assert results
    assert all(r["discovery_source"] == "rbi_registry" for r in results)


def test_search_unknown_city_or_type():
    assert store.search("Atlantis", "Banks") == []
    assert store.search("Jaipur", "Corporates") == []


def test_by_cin_roundtrip():
    nbfc = next(r for r in store.search("Jaipur", "NBFCs") if r["cin"])
    row = store.by_cin(nbfc["cin"])
    assert row is not None
    assert row["name"] == nbfc["name"]
    assert store.by_cin("U00000XX0000XXX000000") is None


def test_stats():
    s = store.stats()
    assert s["available"]
    assert s["total"] > 10000
    assert s["by_type"]["NBFC"] > 9000
    assert s["by_type"]["Bank"] > 1400


# ── Geo helpers ───────────────────────────────────────────────────────────────

def test_state_from_cin():
    assert state_from_cin("L65191TN1979PLC007874") == "Tamil Nadu"
    assert state_from_cin("U65923RJ2010PTC031382") == "Rajasthan"
    assert state_from_cin("") == ""
    assert state_from_cin("short") == ""


def test_state_from_pin():
    assert state_from_pin("302001") == "Rajasthan"
    assert state_from_pin("400001") == "Maharashtra"
    assert state_from_pin("") == ""


def test_extract_pin_and_city():
    addr = "Sri Towers, Guindy, Chennai - 600032, Tamil Nadu"
    assert extract_pin(addr) == "600032"
    assert extract_city(addr) == "Chennai"
    assert extract_city("nowhere special", fallback="Jaipur") == "Jaipur"


def test_jitter_is_deterministic_and_small():
    lat, lng = city_coords("jaipur")
    a = jitter(lat, lng, "company-1")
    b = jitter(lat, lng, "company-1")
    c = jitter(lat, lng, "company-2")
    assert a == b
    assert a != c
    assert abs(a[0] - lat) < 0.05 and abs(a[1] - lng) < 0.05
