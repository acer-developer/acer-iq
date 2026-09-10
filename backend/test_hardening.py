"""Runnable check for the Phase-2 hardening: failures must be visible.

    python -m backend.test_hardening
"""
import asyncio
from collections import Counter

from backend import main
from backend.pipeline import winnability, lead_queue


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


def test_catch_all_route_never_shadows_the_api():
    """The browser catch-all must be registered last, or "/{path:path}" eats
    /api/* and /docs and the whole API 307s to the frontend."""
    paths = [getattr(r, "path", "") for r in main.app.routes]
    catch_alls = [i for i, p in enumerate(paths) if "{path:path}" in p]
    if not catch_alls:
        return  # a frontend build is present, so no redirect routes exist
    first_catch_all = min(catch_alls)
    for i, p in enumerate(paths):
        if p.startswith("/api/") or p in ("/docs", "/openapi.json"):
            assert i < first_catch_all, f"{p} is shadowed by the catch-all"


def test_health_is_not_on_the_root_path():
    """Liveness lives at /api/health so "/" stays free to serve the UI."""
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert "/api/health" in paths


# ── Winnability (ROADMAP_V3 lever 3) ─────────────────────────────────────────

def test_unverified_sources_never_look_like_a_first_timer():
    """The whole point of data_status: absence of ratings is not evidence."""
    blind = {"rated_by_count": 0, "data_status": "unverified",
             "agencies": [], "rating_actions": []}
    r = winnability.score({}, blind)
    assert r["flags"]["first_timer"] is False
    assert r["blocked"] is True, "unverified credit must not reach BD"


def test_agency_forced_withdrawal_is_not_a_buy_signal():
    """Only a withdrawal the ISSUER asked for means they are shopping."""
    forced = {"rated_by_count": 1, "data_status": "ok", "agencies": [],
              "rating_actions": [{"action": "Withdrawn due to non-cooperation",
                                  "rating": "CARE BBB"}]}
    assert winnability.score({}, forced)["flags"]["self_withdrawn"] is False


def test_distressed_issuer_is_blocked_not_suppressed():
    """Adverse selection guard: winnable on paper, still must not be called."""
    distressed = {
        "rated_by_count": 1, "data_status": "ok",
        "agencies": [{"instruments": [{"rating": "CRISIL D", "status": "Downgraded"}]}],
        "rating_actions": [{"action": "Issuer not cooperating", "rating": "CRISIL D"}],
    }
    r = winnability.score({}, distressed)
    assert r["blocked"] is True and r["suppressed"] is False


def test_company_credit_response_carries_winnability():
    import inspect
    src = inspect.getsource(main.company_credit)
    assert '"winnability": win' in src


# ── Lead queue (ROADMAP_V3 phase 3) ──────────────────────────────────────────

def test_queue_merges_name_variants_of_one_issuer():
    """Two spellings must not become two leads, or two people call one company."""
    rows = lead_queue.build_rows([
        {"agency": "ACUITE", "company_name": "Spectron Engineers Private Limited",
         "rating": "ACUITE BBB", "action": "Reaffirmed", "date": "09-09-2026"},
        {"agency": "BRICKWORK", "company_name": "SPECTRON ENGINEERS PVT LTD",
         "rating": "BWR BBB", "action": "Reaffirmed", "date": "01-09-2026"},
    ])
    assert len(rows) == 1 and rows[0]["action_count"] == 2, rows


def test_queue_never_reports_a_first_timer():
    """A company only reaches this queue by having a published action, so it
    cannot be an unrated first-time issuer. Guards against a future weight
    change quietly inventing them."""
    rows = lead_queue.build_rows([
        {"agency": "ACUITE", "company_name": "Anyone Ltd", "rating": "ACUITE A",
         "action": "Assigned", "date": "09-09-2026"},
    ])
    assert not any(r["flags"]["first_timer"] for r in rows)


def test_queue_sinks_blocked_leads_below_workable_ones():
    """The queue is worked top-down, so an uncallable lead must never head it."""
    rows = lead_queue.build_rows([
        {"agency": "ACUITE", "company_name": "Distressed Ltd", "rating": "ACUITE D",
         "action": "Issuer not cooperating", "date": "09-09-2026"},
        {"agency": "ACUITE", "company_name": "Healthy Ltd", "rating": "ACUITE AA",
         "action": "Reaffirmed", "date": "08-09-2026"},
    ])
    assert rows[-1]["company_name"] == "Distressed Ltd" and rows[-1]["blocked"]


def test_queue_endpoint_exists_and_rejects_a_silly_window():
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert "/api/queue" in paths
    import inspect
    assert "days must be between 1 and 365" in inspect.getsource(main.get_queue)


# ── Credit-screen grade boundaries ───────────────────────────────────────────

def _rating(r):
    return {"rated_by_count": 1, "data_status": "ok", "agencies": [],
            "rating_actions": [{"action": "x", "rating": r}]}


def test_investment_grade_is_not_read_as_speculative():
    """BBB must never match the B/BB rules. Getting this wrong would block every
    investment-grade issuer in the queue and leave nothing callable."""
    for r in ["CARE BBB; Stable", "ACUITE BBB+", "IND BBB-", "CARE AAA", "CARE A1+"]:
        assert winnability.credit_screen(_rating(r))["pass"] is True, r


def test_speculative_grades_are_caught():
    for r in ["ACUITE D", "BWR BB", "IND B+", "ACUITE C"]:
        assert winnability.credit_screen(_rating(r))["pass"] is False, r


# ── India Ratings title parsing ──────────────────────────────────────────────

def test_indra_rating_keeps_its_modifier():
    """'IND BB+' must not degrade to 'IND BB' - a lost +/- moves the grade."""
    from backend.pipeline.cra_press import parse_indra_json
    rows = [{"issuerName": "X Ltd", "pressReleaseID": 1, "prDate": "Sep 10, 2026",
             "pressReleaseTitle": "India Ratings Affirms X at 'IND BB+'/Stable"}]
    assert parse_indra_json(rows)[0]["rating"] == "IND BB+"


def test_indra_non_cooperation_becomes_an_inc_action():
    from backend.pipeline.cra_press import parse_indra_json
    rows = [{"issuerName": "Y Ltd", "pressReleaseID": 2, "prDate": "Jan 02, 2026",
             "pressReleaseTitle": "India Ratings Migrates Y to Non-Cooperating Category"}]
    assert parse_indra_json(rows)[0]["action"] == "Issuer Not Cooperating"


def test_care_never_invents_a_date():
    """CARE's payload has no date. A fabricated one would drop a stale rating
    inside a 'last 30 days' window and make it look like fresh news."""
    from backend.pipeline.cra_press import parse_care_ratings
    out = parse_care_ratings({"data": [{"Company": "Z Ltd", "CompanyInstrument": [
        {"Instrument": "Term Loan", "Rating": "CARE BBB; Stable"}]}]})
    assert out[0]["date"] == "" and out[0]["action"] == "Current rating"


def test_care_lookup_refuses_a_different_company():
    """CARE's autocomplete is a substring search, so it will happily return a
    different company with a similar name. Attaching another company's rating -
    or another company's default - to a lead someone is about to call is the
    worst error this module can make."""
    from backend.pipeline.cra_press import _care_match
    assert _care_match("Berar Finance Ltd", "Berar Finance Limited") is True
    assert _care_match("Adani Agri Fresh", "Adani Agri Fresh Limited") is True
    assert _care_match("Arka Eduserve Private Limited",
                       "Arka Educational & Cultural Trust") is False
    assert _care_match("Tata Motors Limited", "Tata Steel Limited") is False
    assert _care_match("", "Anything Ltd") is False


def test_care_lookups_are_cached_including_misses():
    """The queue enriches 15 leads per build, so an uncached CARE lookup means
    30 calls on every page load and a rate-limit ban. Misses must cache too, or
    the companies CARE does not rate get retried forever."""
    from backend.pipeline import cra_press

    calls = {"n": 0}

    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    async def fake_get(url, params=None, **kw):
        calls["n"] += 1
        if "searchlist" in url:
            return _Resp({"data": [{"CompanyID": "tok", "CompanyName": "Cached Co Limited"}]})
        return _Resp({"data": [{"Company": "Cached Co Limited", "CompanyInstrument": [
            {"Instrument": "Term Loan", "Rating": "CARE BBB; Stable"}]}]})

    class _Client:
        get = staticmethod(fake_get)

    real_client, real_cache = cra_press._get_client, dict(cra_press._cache)
    cra_press._get_client = lambda: _Client()
    cra_press._cache.clear()
    try:
        first = asyncio.run(cra_press.fetch_care_for_company("Cached Co Limited"))
        after_first = calls["n"]
        second = asyncio.run(cra_press.fetch_care_for_company("Cached Co Ltd"))
        assert after_first == 2, f"first lookup should cost 2 calls, cost {after_first}"
        assert calls["n"] == after_first, "repeat lookup must not hit the network"
        assert first == second and len(first) == 1
    finally:
        cra_press._get_client = real_client
        cra_press._cache.clear()
        cra_press._cache.update(real_cache)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("all hardening checks passed")
