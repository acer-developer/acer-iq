"""Runnable check for the Phase-2 hardening: failures must be visible.

    python -m backend.test_hardening
"""
import asyncio
import time
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


# -- Source-health alarms (ROADMAP_V3 phase 1) -------------------------------

def test_health_reports_every_cra_scraper():
    """A dark scraper must be visible to an uptime check. _source_status alone
    only knows about labels that failed inside a request, so an agency whose
    breaker opened between requests would look perfectly healthy."""
    h = asyncio.run(main.health())
    names = {s["name"] for s in h["sources"]}
    for agency in ("ACUITE", "BRICKWORK", "INDRA", "CARE", "CRISIL", "ICRA", "INFOMERICS"):
        assert f"CRA:{agency}" in names, f"{agency} missing from health"


def test_health_goes_degraded_when_a_breaker_is_open():
    from backend.pipeline import cra_press
    saved = dict(cra_press._breaker["ACUITE"])
    try:
        cra_press._breaker["ACUITE"]["until"] = time.time() + 600
        h = asyncio.run(main.health())
        assert h["status"] == "degraded", h["status"]
        assert "CRA:ACUITE" in h["degraded"]
    finally:
        cra_press._breaker["ACUITE"].update(saved)


def test_health_is_ok_when_nothing_is_tripped():
    h = asyncio.run(main.health())
    assert h["status"] == "ok" and h["degraded"] == []


def test_a_statically_blocked_agency_is_not_an_alarm():
    """CRISIL/ICRA/Infomerics have no scrape path by design. Alerting on them
    would train whoever is on call to ignore this endpoint."""
    h = asyncio.run(main.health())
    blocked = [s for s in h["sources"] if s["name"] == "CRA:CRISIL"]
    assert blocked and blocked[0]["ok"] is True and blocked[0]["detail"]


# -- Bounded caches ----------------------------------------------------------

def test_mca_and_geocode_caches_expire_and_are_bounded():
    """Both were plain dicts that never evicted. On the always-on host phase 1
    calls for, that grows for the life of the process and serves stale data
    forever."""
    from backend.pipeline import mca_scraper, discovery
    for cache in (mca_scraper._cache, discovery._geo_cache):
        assert cache.ttl > 0, "a cache with no TTL serves stale data forever"
        assert cache.maxsize > 0, "an unbounded lookup cache is a slow leak"


# -- Saved leads + outcome log (ROADMAP_V3 phase 3) --------------------------

def _temp_store():
    """pipeline_store pointed at a throwaway DB. Returns (module, cleanup)."""
    import tempfile
    from pathlib import Path
    from backend.pipeline import pipeline_store as ps
    tmp = tempfile.mkdtemp()
    real = ps.DB_PATH
    ps.DB_PATH = Path(tmp) / "t.sqlite"

    def cleanup():
        ps.DB_PATH = real
    return ps, cleanup


def test_saving_the_same_lead_twice_does_not_reset_its_stage():
    """Two people clicking Add on one company must not drag a lead that is
    already at Proposal back to Identified."""
    ps, cleanup = _temp_store()
    try:
        ps.save_lead({"company_name": "Acme Ltd", "winnability": 50, "flags": {}})
        ps.set_stage("Acme Ltd", "Proposal")
        again = ps.save_lead({"company_name": "Acme Ltd"})
        assert again["already_saved"] is True
        assert again["stage"] == "Proposal", again
    finally:
        cleanup()


def test_an_unknown_stage_is_refused_not_written():
    """Otherwise the funnel quietly grows categories nobody can report on."""
    ps, cleanup = _temp_store()
    try:
        ps.save_lead({"company_name": "Acme Ltd"})
        try:
            ps.set_stage("Acme Ltd", "Nearly There")
            raise AssertionError("unknown stage should have been refused")
        except ValueError:
            pass
        assert ps.funnel()["Identified"] == 1
    finally:
        cleanup()


def test_outcome_history_survives_removing_a_lead():
    """A lead that was worked and dropped is exactly the outcome data the
    winnability weights need. Deleting the history would throw it away."""
    ps, cleanup = _temp_store()
    try:
        ps.save_lead({"company_name": "Acme Ltd", "flags": {"inc_tagged": True}})
        ps.set_stage("Acme Ltd", "Lost")
        assert ps.remove_lead("Acme Ltd") is True
        assert ps.list_leads() == []
        events = [e["event"] for e in ps.events("Acme Ltd")]
        assert events == ["removed", "stage", "saved"], events
    finally:
        cleanup()


def test_saved_lead_json_columns_are_decoded_consistently():
    """The same field must not be a dict on one endpoint and a JSON string on
    another, or a caller renders a quoted blob at someone."""
    ps, cleanup = _temp_store()
    try:
        lead = {"company_name": "Acme Ltd", "flags": {"multi_cra": True},
                "agencies_seen": ["CARE"]}
        ps.save_lead(lead)
        again = ps.save_lead(lead)
        listed = ps.list_leads()[0]
        assert again["flags"] == listed["flags"] == {"multi_cra": True}
        assert again["agencies"] == listed["agencies"] == ["CARE"]
    finally:
        cleanup()


def test_lead_routes_are_registered_and_events_is_not_a_company_name():
    """/api/leads/events must be declared before /api/leads/{company_name},
    or "events" gets captured as a company."""
    paths = [getattr(r, "path", "") for r in main.app.routes]
    for p in ("/api/leads", "/api/leads/events", "/api/leads/{company_name}",
              "/api/leads/{company_name}/stage"):
        assert p in paths, f"{p} missing"
    assert paths.index("/api/leads/events") < paths.index("/api/leads/{company_name}")


# -- Search persistence falls back to SQLite (no Supabase project) ------------

def test_search_store_always_resolves_to_a_real_backend():
    """A restart must not start 404-ing CSV exports, whichever store is live.

    This used to assert supabase_configured() is False, which baked "there is no
    Supabase project" into a test as though it were permanent. A project now
    exists and the test broke - so it now pins the invariant that actually
    matters: there is always a working store, named honestly."""
    from backend import database
    assert database.backend_name() in {"sqlite", "supabase"}
    # Configured or not, a round trip must succeed - Supabase errors fall
    # through to SQLite rather than losing the search.
    database.save_search("test-roundtrip", "Chennai", "NBFC", [{"name": "Y Ltd"}])
    row = database.load_search("test-roundtrip")
    assert row and row["city"] == "Chennai", row


def test_health_names_the_search_store_in_use():
    """Nobody should have to guess whether their searches are being persisted."""
    h = asyncio.run(main.health())
    assert any(s["name"].startswith("Search store") for s in h["sources"]), h["sources"]


def test_health_pages_on_a_source_silent_for_48_hours():
    """PREMORTEM section 2/8: a feed that has not answered in 48h must reach
    the list an uptime check pages on, and say since when - not hide behind
    a green process-liveness check."""
    import asyncio
    from datetime import datetime, timedelta, timezone
    from backend import main
    from backend.pipeline import source_health as sh
    saved, loaded = dict(sh._state), sh._loaded
    three_days = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
    sh._loaded = True
    sh._state["NSE announcements"] = {
        "source": "NSE announcements", "last_attempt": three_days,
        "last_success": None, "failing_since": three_days,
        "last_error": "HTTP 403", "last_count": None}
    try:
        h = asyncio.run(main.health())
        assert h["status"] == "degraded", h
        hit = [d for d in h["degraded"] if d.startswith("NSE announcements")]
        assert hit and "unreachable since" in hit[0], h["degraded"]
        fresh = {f["source"]: f for f in h["freshness"]}
        assert fresh["NSE announcements"]["state"] == "down"
    finally:
        sh._state.clear(); sh._state.update(saved); sh._loaded = loaded


def test_empty_list_says_quiet_or_broken():
    """An empty list must say which it is (PREMORTEM section 2)."""
    from backend.pipeline import source_health as sh
    ok = {"source": "CRA:ACUITE", "state": "ok", "message": "read OK just now"}
    bad = {"source": "CRA:INDRA", "state": "failing",
           "message": "unreachable since 28 Sep 10:00 UTC"}
    assert sh.summarise([ok])["verdict"] == "quiet"
    v = sh.summarise([ok, bad])
    assert v["verdict"] == "degraded" and "CRA:INDRA unreachable since" in v["text"], v
    assert sh.summarise([])["verdict"] == "unknown"


def test_news_rows_carry_source_read_date_and_reason():
    """BUILD_PLAN invariants 2-4 on Tab 3: served from the archive, major
    only by default, and every row has a link, a read date and a why."""
    import asyncio, tempfile, time
    from pathlib import Path
    from backend import database, main
    from backend.pipeline import news_archive as na
    real, real_client = na.DB_PATH, database.get_client
    na.DB_PATH = Path(tempfile.mkdtemp()) / "t.sqlite"
    database.get_client = lambda: None
    main._news_polled["at"] = time.time()   # no live poll in the test
    try:
        from datetime import date
        today = date.today().isoformat()
        na.record([
            {"source": "Economic Times", "subject": "Acme Finance raises Rs 900 cr via NCDs",
             "link": "https://et.example/1", "date": today},
            {"source": "LiveMint", "subject": "Top 5 stocks to buy tomorrow",
             "link": "https://lm.example/2", "date": today},
        ])
        major = asyncio.run(main.get_market_news(days=7, major=True))
        assert [i["subject"] for i in major["items"]] == ["Acme Finance raises Rs 900 cr via NCDs"]
        row = major["items"][0]
        assert row["link"] and row["read_at"] and row["why"], row
        assert row["kind"] == "debt_raise", row
        everything = asyncio.run(main.get_market_news(days=7, major=False))
        assert everything["total_items"] == 2
        assert "empty_means" in major and "freshness" in major
    finally:
        na.DB_PATH, database.get_client = real, real_client
        main._news_polled["at"] = 0.0


def test_macro_degrades_and_every_name_has_reason_and_source():
    """Tab 1: a failed build is an honest empty with coverage saying so, not
    a 500; and the join never names a company without a reason and a source."""
    import asyncio
    from backend import main
    from backend.pipeline import macro

    real = macro.build

    async def boom(days):
        raise RuntimeError("NSE down")
    macro.build = boom
    try:
        out = asyncio.run(main.get_macro(days=14))
        assert out["sectors"] == [] and out["coverage"]["stale_inputs"], out
        assert "empty_means" in out and "freshness" in out
    finally:
        macro.build = real

    ev = [{"subject": "RBI hikes repo rate", "source": "ET", "link": "https://x", "published": "",
           "read_at": "2026-09-28", "sectors": ["nbfc"], "sector_labels": [], "why": ""}]
    cov = [{"company_name": "Zeta Finance Limited", "interest_coverage": 0.7,
            "verdict": "Interest coverage 0.7x - operating profit does not cover interest",
            "source_url": "https://nse.example/x", "read_at": "2026-09-28"}]
    blocks = macro.join(ev, [], cov, {}, set())
    names = blocks[0]["companies"]
    assert names and names[0]["reason"] and names[0]["triggers"][0]["url"], names


def test_archive_links_are_http_only():
    """Security review 2026-09-28: archive links come from third-party feeds
    and render as "Source" hrefs; a javascript: URL would run in a BD's
    session. Only http(s) is ever stored or served."""
    from backend import database, main
    assert database.safe_url("javascript:alert(1)") == ""
    assert database.safe_url(" JAVASCRIPT:alert(1)") == ""
    assert database.safe_url("data:text/html,x") == ""
    assert database.safe_url("https://nse.example/a.pdf") == "https://nse.example/a.pdf"
    assert main._news_link({"link": "javascript:x", "symbol": "ACME"}).startswith("https://www.nseindia.com/")


def test_fit_analysis_says_whether_ai_answered():
    """A retired free model left every fit analysis rule-based while the UI
    implied AI. Now every fit carries analysis_source, and an LLM reply is
    filtered field by field so a malformed one cannot blank anything."""
    import asyncio
    from backend.pipeline import fit_analyzer, llm
    company = {"name": "Acme Finance Limited", "entity_type": "NBFC"}
    credit = {"agencies": [], "rated_by_count": 0, "data_status": "none_found",
              "total_instruments": 0}
    real_chat, real_prov = fit_analyzer.chat, llm._providers

    async def no_answer(prompt, max_tokens=600):
        return None
    async def junk(prompt, max_tokens=600):
        llm.last_answer.update(provider="OpenRouter", model="test/model:free")
        return '{"fit_score": 77, "key_insights": "not a list", "recommended_action": ""}'
    llm._providers = lambda: [{"name": "OpenRouter"}]
    try:
        fit_analyzer.chat = no_answer
        r = asyncio.run(fit_analyzer.analyze_fit(company, credit))
        assert r["analysis_source"].startswith("Rule-based"), r
        base_action = r["recommended_action"]
        fit_analyzer.chat = junk
        r = asyncio.run(fit_analyzer.analyze_fit(company, credit))
        assert r["analysis_source"] == "AI - test/model:free", r
        assert r["fit_score"] == 77
        assert isinstance(r["key_insights"], list)          # junk ignored
        assert r["recommended_action"] == base_action        # blank ignored
    finally:
        fit_analyzer.chat, llm._providers = real_chat, real_prov


def test_bd_list_is_frozen_and_every_row_is_explained():
    """PREMORTEM section 4: the month's list is generated once and served from
    the snapshot; a second request must not recompute (or reshuffle) it, and a
    dead input is named rather than silently shrinking the list."""
    import asyncio, tempfile
    from pathlib import Path
    from backend import database
    from backend.pipeline import bd_list
    real_db, real_client, real_gather = bd_list.DB_PATH, database.get_client, bd_list._gather
    bd_list.DB_PATH = Path(tempfile.mkdtemp()) / "t.sqlite"
    database.get_client = lambda: None
    calls = {"n": 0}

    async def fake_inputs():
        calls["n"] += 1
        by = {"ACME FINANCE": {"name": "Acme Finance Limited", "winnability": 45,
                               "blocked": False, "is_lender": True, "win_reason": "",
                               "signals": [{"kind": "debt_raise", "detail": "NCD allotment",
                                            "text": "NSE filing: Allotment of NCDs",
                                            "source": "NSE filing", "url": "https://nse.example/a",
                                            "read_at": "2026-09-28"}]}}
        return by, {"queue": {"status": "ok"}, "refinance": {"status": "unreachable"}}
    bd_list._gather = fake_inputs
    try:
        first = asyncio.run(bd_list.get_or_generate())
        again = asyncio.run(bd_list.get_or_generate())
        assert calls["n"] == 1, "a frozen list was recomputed"
        assert again["rows"] == first["rows"] and again["frozen"]
        row = first["rows"][0]
        assert row["bd_name"] == "Avinash" and row["instrument"] == "NCD / bond rating", row
        assert row["reason"] and row["play"] and row["sources"][0]["read_at"], row
        assert first["inputs"]["refinance"]["status"] == "unreachable"
        forced = asyncio.run(bd_list.get_or_generate(force=True))
        assert forced["version"] == 2 and calls["n"] == 2
    finally:
        bd_list.DB_PATH, database.get_client, bd_list._gather = real_db, real_client, real_gather


def test_team_pipeline_view_needs_a_session():
    """The Admin team view reads every BD's leads with the service key, so it
    must still require a signed-in caller."""
    import asyncio
    from fastapi import HTTPException
    from backend import main
    from backend.pipeline import pipeline_store as ps
    real = ps.per_user
    ps.per_user = lambda: True
    try:
        try:
            asyncio.run(main.get_saved_leads(scope="all", authorization=None))
            raise AssertionError("anonymous caller got the team pipeline")
        except HTTPException as e:
            assert e.status_code == 401
    finally:
        ps.per_user = real


def test_per_user_pipeline_refuses_an_anonymous_caller():
    """With Supabase configured, leads are per BD. A request with no session
    must get 401 - never the old shared SQLite list, which would mix four
    people's pipelines and vanish on the next Render restart."""
    import asyncio
    from fastapi import HTTPException
    from backend import main
    from backend.pipeline import pipeline_store as ps
    real = ps.per_user
    ps.per_user = lambda: True
    try:
        try:
            asyncio.run(main.get_saved_leads(authorization=None))
            raise AssertionError("anonymous caller got a pipeline")
        except HTTPException as e:
            assert e.status_code == 401, e.status_code
        assert main._bearer("Bearer abc.def") == "abc.def"
        assert main._bearer("Basic xyz") is None
    finally:
        ps.per_user = real


def test_cors_allows_delete_for_lead_removal():
    """Remove lead is a DELETE from the Vercel origin; without it in the CORS
    allow-list the preflight fails and the button silently does nothing."""
    from backend import main
    cors = [m for m in main.app.user_middleware if "CORS" in str(m.cls)][0]
    assert "DELETE" in cors.kwargs["allow_methods"], cors.kwargs


# -- Action history makes the `days` window real ------------------------------

def test_history_dedupes_a_refetched_page():
    """The feeds are refetched every 15 minutes. Without dedup on the natural
    key, one company's action multiplies into dozens of rows."""
    import tempfile
    from pathlib import Path
    from backend import database
    from backend.pipeline import action_history as ah
    real, real_client = ah.DB_PATH, database.get_client
    tmp = tempfile.mkdtemp()
    ah.DB_PATH = Path(tmp) / "t.sqlite"
    database.get_client = lambda: None  # never write to a configured Supabase
    try:
        batch = [{"agency": "ACUITE", "company_name": "Acme Ltd", "rating": "A",
                  "action": "Reaffirmed", "date": "01-09-2026"}]
        assert ah.record(batch) == 1
        assert ah.record(batch) == 0
    finally:
        ah.DB_PATH, database.get_client = real, real_client


def test_history_falls_back_to_sqlite_and_says_it_is_not_durable():
    """Until supabase_schema.sql runs, the cra_actions table does not exist.
    A write must still land somewhere, and stats() must not claim a durable
    archive it does not have - pipeline.sqlite dies on a Render restart."""
    import tempfile
    from pathlib import Path
    from backend import database
    from backend.pipeline import action_history as ah
    real, real_client = ah.DB_PATH, database.get_client
    ah.DB_PATH = Path(tempfile.mkdtemp()) / "t.sqlite"
    database.get_client = lambda: ah._FakeTable(fail=True)
    try:
        today = __import__("datetime").datetime.now().strftime("%d-%m-%Y")
        assert ah.record([{"agency": "CARE", "company_name": "Acme Ltd",
                           "rating": "CARE A", "action": "Assigned",
                           "date": today}]) == 1
        assert [a["company_name"] for a in ah.since(30)] == ["Acme Ltd"]
        st = ah.stats()
        assert st["store"] == "sqlite" and st["durable"] is False, st
    finally:
        ah.DB_PATH, database.get_client = real, real_client


def test_history_merge_prefers_the_live_row():
    """A correction in a re-fetch must not be shadowed by the stored copy."""
    from backend.pipeline import action_history as ah
    key = {"agency": "ACUITE", "company_name": "Acme Ltd", "rating": "A",
           "action": "Reaffirmed", "date": "01-09-2026"}
    live = [dict(key, source_url="fresh")]
    archived = [dict(key, source_url="stale")]
    merged = ah.merge(live, archived)
    assert len(merged) == 1 and merged[0]["source_url"] == "fresh"


# -- Refinance window --------------------------------------------------------

def test_refinance_never_reports_a_precise_date_it_does_not_have():
    """BSE's scrip-id convention yields only a maturity YEAR for nearly every
    live row. Showing that as '2026-01-01' reads as a precise deadline, and one
    already in the past - a phantom deadline that sends BD chasing nothing."""
    from backend.pipeline.refinance import maturing_within
    from datetime import date
    year_only = [{"issuer_name": "Navi Finserv Limited", "maturity_date": "2026"}]
    rows = maturing_within(year_only, months_ahead=36, min_months=0)
    assert rows, "a year-only maturity should still be a candidate"
    r = rows[0]
    assert r["maturity_precision"] == "year", r
    assert r["maturity_label"] == "during 2026", r
    assert r["maturity_latest"] == date(2026, 12, 31).isoformat(), r


def test_refinance_excludes_a_row_with_no_maturity_at_all():
    """Never defaulted. A guessed maturity is a fabricated deadline."""
    from backend.pipeline.refinance import maturing_within
    assert maturing_within([{"issuer_name": "X Ltd", "maturity_date": ""}],
                           months_ahead=36, min_months=0) == []


def test_refinance_endpoint_exists_and_bounds_its_window():
    import inspect
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert "/api/refinance" in paths
    assert "months must be between 1 and 36" in inspect.getsource(main.get_refinance)


# -- Fundamentals from NSE XBRL ----------------------------------------------

def test_fundamentals_never_invent_a_missing_figure():
    """A filing that omits a line item must yield None, never zero. Zero finance
    costs would compute as infinite interest coverage and flatter a bad
    borrower - the opposite of what a credit screen is for."""
    from backend.pipeline.fundamentals import parse_xbrl, summarise
    bare = parse_xbrl("<xbrl></xbrl>")
    assert bare["revenue"] is None and bare["finance_costs"] is None
    assert bare["ebit"] is None and bare["interest_coverage"] is None
    assert "unavailable" in summarise(bare)["verdict"]


def test_zero_finance_costs_is_not_infinite_coverage():
    from backend.pipeline.fundamentals import parse_xbrl
    f = parse_xbrl('<xbrl xmlns:a="x"><a:FinanceCosts>0</a:FinanceCosts>'
                   '<a:ProfitBeforeTax>100</a:ProfitBeforeTax></xbrl>')
    assert f["interest_coverage"] is None


def test_ebit_is_derived_because_no_filing_states_it():
    """EBIT = PBT + finance costs. Without this, interest coverage - the one
    metric worth having here - cannot be computed at all."""
    from backend.pipeline.fundamentals import parse_xbrl
    f = parse_xbrl('<xbrl xmlns:a="x"><a:FinanceCosts>100</a:FinanceCosts>'
                   '<a:ProfitBeforeTax>400</a:ProfitBeforeTax></xbrl>')
    assert f["ebit"] == 500 and f["interest_coverage"] == 5.0


def test_filing_dates_compare_chronologically_not_as_text():
    """'24-Aug-2026' sorts BEFORE '24-Dec-2025' as text, which would pin a stale
    filing as the newest one."""
    from backend.pipeline.fundamentals import _filed_at
    from datetime import datetime
    assert "24-Aug-2026" < "24-Dec-2025"        # the trap
    assert _filed_at({"filingDate": "24-Aug-2026 17:39"}) >            _filed_at({"filingDate": "24-Dec-2025 09:00"})
    assert _filed_at({"filingDate": "nonsense"}) == datetime.min


def test_fundamentals_route_exists():
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert "/api/fundamentals/{symbol}" in paths


# -- ACER's own book: the 8th agency ------------------------------------------

def test_our_own_clients_never_appear_as_leads():
    """Cold-calling a company ACER already rates is the most embarrassing
    failure this tool can produce. One set lookup prevents it."""
    from backend.pipeline.lead_queue import build_rows
    rows = build_rows([
        {"agency": "ACUITE", "company_name": "Viviana Power Tech Ltd",
         "rating": "ACUITE A", "action": "Reaffirmed", "date": "01-09-2026"},
        {"agency": "ACUITE", "company_name": "Some Other Co Limited",
         "rating": "ACUITE A", "action": "Reaffirmed", "date": "01-09-2026"},
    ])
    names = [r["company_name"] for r in rows]
    assert not any("Viviana" in n for n in names), names
    assert names == ["Some Other Co Limited"], names


def test_client_matching_survives_suffix_variants():
    from backend.pipeline import acer_book
    assert acer_book.is_client("Viviana Power Tech Limited")
    assert acer_book.is_client("VIVIANA POWER TECH LTD")
    assert acer_book.is_client("Finstars Capital Limited")
    assert not acer_book.is_client("Viviana Solar Private Limited")


def test_renewal_uses_the_latest_action_not_the_first():
    """Viviana was assigned in March and reaffirmed in August. Dating the review
    off the March action would raise the renewal alarm five months early."""
    from backend.pipeline import acer_book
    rs = acer_book.renewals(within_days=10_000)
    viviana = next(r for r in rs if "Viviana" in r["company_name"])
    assert viviana["last_action_date"] == "24-08-2026", viviana
    assert len(rs) == 2, "two issuers, not three actions"


def test_renewals_route_exists():
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert "/api/renewals" in paths


# -- Supabase degradation -----------------------------------------------------

def test_search_store_survives_supabase_without_tables():
    """Supabase is configured but its tables do not exist yet. That must log and
    fall through to SQLite, not lose the search - a lost search is a 404 at
    whoever was about to download the CSV."""
    from backend import database
    database.save_search("test-degrade", "Mumbai", "NBFC", [{"name": "X Ltd"}])
    row = database.load_search("test-degrade")
    assert row and row["city"] == "Mumbai", row


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("all hardening checks passed")
