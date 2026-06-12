"""Tests for rule-based scoring, batch LLM application, JSON parsing, netutil."""

import time

from backend.netutil import SourceStatus, TTLCache
from backend.pipeline.llm import parse_json
from backend.pipeline.scorer import _rule_based_score, apply_batch_scores


# ── Rule-based scorer ─────────────────────────────────────────────────────────

def _nbfc(**over):
    c = {
        "name": "Test Finance Ltd", "entity_type": "NBFC",
        "registry_layer": "Upper", "deposit_taking": True,
        "past_instruments": [], "cin": "U65923RJ2010PTC031382",
        "incorporation_date": "",
    }
    c.update(over)
    return c


def test_rule_based_upper_layer_nbfc_scores_high():
    c = _rule_based_score(_nbfc())
    assert c["score"] >= 60
    assert c["score_label"] in ("Warm Lead", "Hot Lead")
    assert any("Upper" in r for r in c["why_quality_lead"])
    assert c["recommended_approach"]


def test_rule_based_acer_client_floor():
    c = _rule_based_score(_nbfc(past_instruments=[
        {"rating_agency": "INFOMERICS Valuation"},
    ]))
    assert c["score"] >= 78
    assert "renewal" in c["recommended_approach"].lower()


def test_rule_based_competitor_coverage_mentioned():
    c = _rule_based_score(_nbfc(past_instruments=[
        {"rating_agency": "CRISIL"}, {"rating_agency": "ICRA"},
        {"rating_agency": "CRISIL"},
    ]))
    assert any("CRISIL" in r for r in c["why_quality_lead"] + c["pain_points"])


def test_rule_based_score_capped():
    c = _rule_based_score(_nbfc(
        past_instruments=[{"rating_agency": "CRISIL"}] * 10,
        incorporation_date="1990-01-01",
    ))
    assert c["score"] <= 97


# ── Batch LLM scoring application ─────────────────────────────────────────────

def test_apply_batch_scores_happy_path():
    companies = [_rule_based_score(_nbfc(name=f"Co {i}")) for i in range(3)]
    parsed = [
        {"i": 0, "score": 91, "why_quality_lead": ["llm reason"],
         "pain_points": ["llm pain"], "recommended_approach": "llm pitch"},
        {"i": 2, "score": 35},
    ]
    applied = apply_batch_scores(companies, parsed)
    assert applied == 2
    assert companies[0]["score"] == 91
    assert companies[0]["score_label"] == "Hot Lead"
    assert companies[0]["why_quality_lead"] == ["llm reason"]
    assert companies[2]["score"] == 35
    assert companies[2]["score_label"] == "Low Priority"
    # untouched company keeps its rule-based result
    assert companies[1]["score"] >= 60
    # missing fields fall back to the rule-based text
    assert companies[2]["recommended_approach"]


def test_apply_batch_scores_rejects_garbage():
    companies = [_rule_based_score(_nbfc())]
    before = companies[0]["score"]
    assert apply_batch_scores(companies, None) == 0
    assert apply_batch_scores(companies, {"score": 99}) == 0
    assert apply_batch_scores(companies, [{"i": 5, "score": 99}]) == 0
    assert apply_batch_scores(companies, [{"i": 0, "score": "high"}]) == 0
    assert apply_batch_scores(companies, ["nonsense", 42]) == 0
    assert companies[0]["score"] == before


# ── LLM JSON extraction ───────────────────────────────────────────────────────

def test_parse_json_plain_and_fenced():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here you go:\n[{"i": 0, "score": 80}]') == [{"i": 0, "score": 80}]
    assert parse_json("no json here") is None
    assert parse_json("") is None


# ── netutil ───────────────────────────────────────────────────────────────────

def test_ttl_cache_expires():
    c = TTLCache()
    c.set("k", "v", ttl=0.05)
    assert c.get("k") == "v"
    time.sleep(0.06)
    assert c.get("k") is None


def test_ttl_cache_negative_results_get_short_ttl():
    c = TTLCache()
    c.set_result("empty", [], ttl=3600)
    c.set_result("full", [1], ttl=3600)
    # both retrievable now; the empty one carries the short negative TTL
    assert c.get("empty") == []
    assert c.get("full") == [1]
    assert c._data["empty"][0] < c._data["full"][0]


def test_ttl_cache_eviction():
    c = TTLCache(maxsize=10)
    for i in range(12):
        c.set(f"k{i}", i, ttl=60)
    assert len(c._data) <= 11


def test_source_status_aggregation():
    s = SourceStatus()
    s.ok("bse")
    s.ok("bse")
    s.fail("bse", "HTTP 500")
    s.skip("hunter", "no API key configured")
    s.fail("llm", "timeout")
    d = s.as_dict()
    assert d["bse"]["status"] == "degraded"
    assert d["bse"]["ok"] == 2 and d["bse"]["failed"] == 1
    assert d["bse"]["detail"] == "HTTP 500"
    assert d["hunter"]["status"] == "skipped"
    assert d["llm"]["status"] == "failed"
