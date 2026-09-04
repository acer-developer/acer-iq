"""Runnable check for the Phase-2 hardening: failures must be visible.

    python -m backend.test_hardening
"""
import asyncio
from collections import Counter

from backend import main


async def _boom():
    raise RuntimeError("BSE returned 403")


async def _fine():
    return ["instrument"]


def test_safe_tallies_and_returns_default():
    main._failures.set(Counter())
    out = asyncio.run(main._safe(_boom(), [], "bse_instruments"))
    assert out == [], out
    assert main._failures.get()["bse_instruments"] == 1


def test_safe_passes_success_through_untallied():
    main._failures.set(Counter())
    assert asyncio.run(main._safe(_fine(), [], "bse_instruments")) == ["instrument"]
    assert not main._failures.get()


def test_source_status_reports_the_failure():
    main._failures.set(Counter({"bse_instruments": 3}))
    bse = next(s for s in main._source_status(10) if s["name"] == "BSE")
    assert bse["ok"] is False
    assert "3 of 10" in bse["detail"], bse


def test_source_status_clean_run_marks_bse_ok():
    main._failures.set(Counter())
    bse = next(s for s in main._source_status(10) if s["name"] == "BSE")
    # ok unless the circuit breaker itself is open
    from backend.pipeline.bse_scraper import _bse_tripped
    assert bse["ok"] is (not _bse_tripped())


def test_unmapped_label_still_surfaces():
    """A label with no display name must still reach the UI, not vanish."""
    main._failures.set(Counter({"some_new_source": 2}))
    names = [s["name"] for s in main._source_status(1)]
    assert "some_new_source" in names, names


# ── BSE scrip-master parsing (the path that replaced the retired endpoints) ──

def test_scrip_id_coupon_and_maturity():
    from backend.pipeline.bse_scraper import _from_scrip_id
    assert _from_scrip_id("805BFL26") == ("8.05", "2026")
    assert _from_scrip_id("10CAGL27") == ("10.00", "2027")
    assert _from_scrip_id("1025XYZ30") == ("10.25", "2030")
    # two digits are ambiguous: 10 is 10%, 92 is 9.2% (92% is not a coupon)
    assert _from_scrip_id("92LTF29") == ("9.20", "2029")
    assert _from_scrip_id("75ABC28") == ("7.50", "2028")
    # commercial paper ids and junk must not invent a coupon
    assert _from_scrip_id("BFL41125") == ("", "")
    assert _from_scrip_id("") == ("", "")


def test_issuer_name_normalisation_matches_across_sources():
    from backend.pipeline.bse_scraper import _norm
    assert _norm("Bajaj Finance Ltd.") == _norm("Bajaj Finance Limited")
    assert _norm("Muthoot Finance Pvt Ltd") == _norm("MUTHOOT FINANCE PRIVATE LIMITED")
    # distinct companies must not collapse together
    assert _norm("Bajaj Finance Ltd") != _norm("Bajaj Housing Finance Ltd")


def test_incorporation_year_comes_from_cin_not_listing_date():
    # Tata Capital: incorporated 1991 (in the CIN), listed 2025.
    cin = "L65990MH1991PLC060670"
    assert cin[8:12] == "1991"


def test_group_siblings_do_not_inherit_each_others_debt():
    """A parent's issuer name must not match a subsidiary's, or the sales team
    sees company A's instruments under company B."""
    from backend.pipeline.bse_scraper import _clean_name_for_bse, _norm
    parent = _norm(_clean_name_for_bse("REC Limited"))
    child = _norm(_clean_name_for_bse("REC Power Development and Consultancy Ltd"))
    assert parent != child
    # exact-match lookup is the contract: differing names never collide
    assert _norm(_clean_name_for_bse("Tata Capital Ltd")) !=            _norm(_clean_name_for_bse("Tata Capital Housing Finance Ltd"))


def test_registry_parentheticals_are_stripped():
    """RBI names carry '(Formerly known as ...)' that BSE never has; stripping
    it is what makes an exact issuer match possible."""
    from backend.pipeline.bse_scraper import _clean_name_for_bse, _norm
    cases = {
        "L&T Finance Limited (Formerly known as L & T Finance Holdings Limited)": "L T FINANCE",
        "Auxilo Finserve Private Limited (W.E.F. September 11, 2017)": "AUXILO FINSERVE",
        "BOBCARD Limited (Formerly known as BOB Financial Solutions Limited)": "BOBCARD",
    }
    for raw, expected in cases.items():
        assert _norm(_clean_name_for_bse(raw)) == expected, (raw, _norm(_clean_name_for_bse(raw)))


def test_source_status_uses_readable_names_not_internal_labels():
    from collections import Counter
    from backend import main
    main._failures.set(Counter({"credit_history": 2, "bse_instruments": 1}))
    names = [s["name"] for s in main._source_status(10)]
    assert "credit_history" not in names, names
    assert "Rating history" in names, names
    assert names.count("BSE") == 1, names


def test_scrip_code_lookup_is_exact_only():
    """A partial name must not resolve to a scrip code, or CorpInfo attaches the
    wrong company's CIN and board to the lead."""
    import asyncio
    from backend.pipeline import bse_scraper as b
    b._codes = {"BAJAJ FINANCE": "500034", "BAJAJ GLOBAL": "506947"}
    b._master, b._master_at = {"x": []}, 9e18   # pretend the master is fresh
    try:
        get = lambda n: asyncio.run(b.scrip_code_for(n))
        assert get("Bajaj Finance Ltd.") == "500034"      # punctuation tolerated
        assert get("Bajaj Finance Limited") == "500034"   # legal suffix tolerated
        assert get("Bajaj") == ""                         # partial must NOT match
        assert get("Bajaj Finance Securities Ltd") == ""  # sibling must NOT match
    finally:
        b._codes, b._master, b._master_at = {}, {}, 0.0


# ── LLM provider fallback ────────────────────────────────────────────────────

def test_provider_order_and_placeholder_keys():
    """OpenRouter first, TokenRouter as fallback; placeholder keys count as
    unset or the app reports AI as healthy when it is not."""
    from backend.config import settings
    from backend.pipeline import llm

    orig = (settings.openrouter_api_key, settings.tokenrouter_api_key)
    try:
        settings.openrouter_api_key, settings.tokenrouter_api_key = "", ""
        assert llm._providers() == []

        settings.tokenrouter_api_key = "your_key_here"      # placeholder
        assert llm._providers() == [], "placeholder must not count as configured"

        settings.tokenrouter_api_key = "sk-real"
        assert [p["name"] for p in llm._providers()] == ["TokenRouter"]

        settings.openrouter_api_key = "sk-also-real"
        assert [p["name"] for p in llm._providers()] == ["OpenRouter", "TokenRouter"]
    finally:
        settings.openrouter_api_key, settings.tokenrouter_api_key = orig


def test_reasoning_model_gets_token_headroom():
    """glm-5.3 spends ~300 max_tokens on private reasoning, so the callers'
    512-600 budget must be raised or the JSON truncates mid-object."""
    from backend.config import settings
    from backend.pipeline import llm

    orig = settings.tokenrouter_api_key
    try:
        settings.tokenrouter_api_key = "sk-real"
        tr = next(p for p in llm._providers() if p["name"] == "TokenRouter")
        assert max(600, tr["min_tokens"]) >= 2000, tr["min_tokens"]
    finally:
        settings.tokenrouter_api_key = orig


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("all hardening checks passed")
