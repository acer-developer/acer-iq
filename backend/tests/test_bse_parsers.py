"""Parser tests for the BSE scrapers against recorded fixture payloads."""

import json
from pathlib import Path

from backend.pipeline.bse_scraper import (
    _clean_name_for_bse, _short_name, parse_debt_response,
)
from backend.pipeline.mca_scraper import is_cin, parse_corp_info

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_debt_response_field_mapping():
    instruments = parse_debt_response(_load("bse_debt_response.json"))
    assert len(instruments) == 3

    first = instruments[0]
    assert first["isin"] == "INE721A07PT4"
    assert first["instrument_type"] == "Non Convertible Debentures"
    assert first["maturity_date"] == "09 Jul 2027"
    assert first["coupon_rate"] == "9.10"
    assert first["credit_rating"] == "CRISIL AA+/Stable"
    assert first["rating_agency"] == "CRISIL"
    assert first["amount_crores"] == "500"

    # third row uses the alternate (CamelCase) key spellings
    alt = instruments[2]
    assert alt["isin"] == "INE721A07PV0"
    assert alt["rating_agency"] == "INFOMERICS"
    assert alt["maturity_date"] == "20 Dec 2026"
    assert alt["face_value"] == "1000"


def test_parse_debt_response_empty_and_malformed():
    assert parse_debt_response({}) == []
    assert parse_debt_response({"Table": []}) == []
    assert parse_debt_response({"Table": None}) == []
    assert parse_debt_response({"data": [{"ISIN_NO": "X"}]})[0]["isin"] == "X"


def test_parse_corp_info_separates_listing_from_incorporation():
    """P6 fix: BSE listing date must never be presented as incorporation date."""
    info = parse_corp_info(_load("bse_corpinfo_response.json"))
    assert info["cin"] == "L65191TN1979PLC007874"
    assert info["listing_date"] == "1995-07-12"
    assert info["incorporation_date"] == ""  # BSE does not know this
    assert info["registered_address"].endswith("Chennai, Tamil Nadu")

    # nameless director row is dropped, real ones keep DIN + designation
    assert len(info["directors"]) == 2
    assert info["directors"][0]["name"] == "Umesh Revankar"
    assert info["directors"][0]["din"] == "00141189"


def test_parse_corp_info_without_cin_returns_empty():
    assert parse_corp_info({"Table3": [{"fld_cin": ""}]}) == {}
    assert parse_corp_info({}) == {}


def test_is_cin():
    assert is_cin("L65191TN1979PLC007874")
    assert is_cin("U65923RJ2010PTC031382")
    assert not is_cin("Shriram Finance")
    assert not is_cin("L65191TN1979PLC0078")  # too short
    assert not is_cin("X65191TN1979PLC007874")  # bad prefix


def test_clean_name_strips_branch_suffixes():
    assert _clean_name_for_bse("State Bank of India - Kochi Branch") == "State Bank of India"
    assert _clean_name_for_bse("HDFC Bank ATM") == "HDFC Bank"
    assert _clean_name_for_bse("Bajaj Finance Ltd. – Regional Office") == "Bajaj Finance Ltd"
    assert _clean_name_for_bse("Shriram Finance Limited") == "Shriram Finance Limited"


def test_short_name_drops_stopwords():
    assert _short_name("The Jaipur Central Co-operative Bank Ltd") == "Jaipur Central Co-operative"
    assert _short_name("Bajaj Finance Limited") == "Bajaj Finance"
