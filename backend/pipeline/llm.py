"""
LLM access for scoring and fit analysis, over OpenAI-compatible chat APIs.

Providers are tried in order and the first usable reply wins, so a dead key or
a rate-limited free tier degrades to the next provider instead of silently
dropping AI analysis:

  1. OpenRouter   - free models, chosen from OpenRouter's LIVE model list
  2. TokenRouter  - fallback, z-ai/glm-5.3-free

With no provider configured, chat() returns None and callers fall back to
rule-based scoring.

WHY THE MODEL IS NOT HARD-CODED ANY MORE (found 2026-09-28): the one model this
used, meta-llama/llama-3.3-70b-instruct:free, was retired by OpenRouter. Every
call errored, every AI field fell back to rules, and nothing on screen said
so. Free models come and go (PREMORTEM.md section 6), so the list is read from
https://openrouter.ai/api/v1/models (public, no key) every 12 hours and the
first preferred model that is actually live is used, falling through the rest
on error. OPENROUTER_MODEL (comma-separated) overrides the choice.

Every answer is attributed: `last_answer` says which provider/model replied,
and source_health records "AI (OpenRouter)" so /api/health shows when AI was
last actually working rather than merely configured.
"""

import json
import logging
import time

import httpx

from backend.config import settings
from backend.pipeline import source_health

log = logging.getLogger("acer-iq.llm")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Instruction-tuned, general-purpose free models, best first (live list,
# 28 Sep 2026). Reasoning-only, code, safety and tiny models are left out:
# they either burn the token budget thinking or cannot follow the JSON shape.
PREFERRED_FREE = [
    "google/gemma-4-31b-it:free",
    "qwen/qwen3.8-27b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-26b-a4b-it:free",
]
_SKIP = ("safety", "code", "omni", "reasoning", "2.6b", "nano", "mini")
_MAX_MODELS_PER_CALL = 3
_LIST_TTL = 12 * 3600

_live = {"at": 0.0, "ids": None}
last_answer: dict = {}

_PLACEHOLDERS = {"", "your_key_here", "your_url_here"}


def _set(value: str) -> bool:
    return bool(value) and value.strip() not in _PLACEHOLDERS


def _providers() -> list[dict]:
    """Configured providers, in fallback order."""
    out: list[dict] = []
    if _set(settings.openrouter_api_key):
        out.append({
            "name":       "OpenRouter",
            "url":        OPENROUTER_URL,
            "key":        settings.openrouter_api_key,
            "model":      None,          # chosen per call by _openrouter_models()
            "min_tokens": 0,
        })
    if _set(settings.tokenrouter_api_key):
        out.append({
            "name":  "TokenRouter",
            "url":   settings.tokenrouter_base_url.rstrip("/") + "/chat/completions",
            "key":   settings.tokenrouter_api_key,
            "model": settings.tokenrouter_model,
            # glm-5.3 is a reasoning model and its private reasoning tokens are
            # charged against max_tokens. Measured ~300 of them for a small
            # JSON answer, so the callers' 512-600 budget truncates the object
            # mid-key and parse_json() gets nothing. Give it real headroom.
            "min_tokens": 2000,
        })
    return out


def choose_models(live_ids: set[str] | None, override: str = "") -> list[str]:
    """Which OpenRouter models to try, in order. Pure, for the self-check."""
    if override.strip():
        return [m.strip() for m in override.split(",") if m.strip()]
    if not live_ids:
        return list(PREFERRED_FREE)       # list unreadable: try the known-good ones
    ordered = [m for m in PREFERRED_FREE if m in live_ids]
    others = sorted(m for m in live_ids if m.endswith(":free") and m not in ordered
                    and not any(k in m.lower() for k in _SKIP))
    return ordered + others


async def _live_free(client: httpx.AsyncClient) -> set[str] | None:
    if _live["ids"] is not None and time.time() - _live["at"] < _LIST_TTL:
        return _live["ids"]
    try:
        r = await client.get(OPENROUTER_MODELS_URL, timeout=20)
        ids = {m["id"] for m in r.json().get("data", []) if m.get("id", "").endswith(":free")}
        if ids:
            _live.update(at=time.time(), ids=ids)
            return ids
    except Exception as e:
        log.warning("OpenRouter model list unavailable: %s: %s", type(e).__name__, e)
    return _live["ids"]


async def chat(prompt: str, max_tokens: int = 600) -> str | None:
    """Send a prompt to the first provider/model that answers. Returns None when
    no provider is configured or all of them fail."""
    providers = _providers()
    if not providers:
        return None

    async with httpx.AsyncClient(timeout=60) as client:
        attempts: list[dict] = []
        for p in providers:
            if p["name"] == "OpenRouter":
                models = choose_models(await _live_free(client),
                                       getattr(settings, "openrouter_model", ""))
                attempts += [p | {"model": m} for m in models[:_MAX_MODELS_PER_CALL]]
            else:
                attempts.append(p)
        for p in attempts:
            try:
                resp = await client.post(
                    p["url"],
                    headers={
                        "Authorization": f"Bearer {p['key']}",
                        "Content-Type":  "application/json",
                        "HTTP-Referer":  "https://acer-iq.vercel.app",
                        "X-Title":       "ACER-IQ",
                    },
                    json={
                        "model":      p["model"],
                        "messages":   [{"role": "user", "content": prompt}],
                        "max_tokens": max(max_tokens, p["min_tokens"]),
                    },
                )
                data = resp.json()

                if isinstance(data, dict) and data.get("error"):
                    log.warning("%s/%s error: %s", p["name"], p["model"], data["error"])
                    continue

                choice = (data.get("choices") or [{}])[0]
                text = ((choice.get("message") or {}).get("content") or "").strip()

                # A truncated reply is worse than no reply: it parses as broken
                # JSON and the caller reports a confident wrong answer.
                if choice.get("finish_reason") == "length" and not text.endswith("}"):
                    log.warning("%s reply truncated at max_tokens - trying next provider",
                                p["name"])
                    continue
                if not text:
                    log.warning("%s returned an empty reply", p["name"])
                    continue

                last_answer.update(provider=p["name"], model=p["model"],
                                   at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                source_health.record(f"AI ({p['name']})", True)
                return text
            except Exception as e:
                log.warning("%s/%s call failed: %s: %s", p["name"], p["model"],
                            type(e).__name__, e)

    for name in {p["name"] for p in providers}:
        source_health.record(f"AI ({name})", False, error="no model answered")
    log.warning("all %d LLM attempt(s) failed", len(attempts))
    last_answer.clear()
    return None


def parse_json(raw: str) -> dict | None:
    """Extract JSON from raw LLM output (handles markdown fences)."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text.strip())
    except Exception:
        # Try to find JSON object within the text
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except Exception:
                pass
    return None


def _demo() -> None:
    live = {"google/gemma-4-31b-it:free", "qwen/qwen3.8-27b:free",
            "nvidia/nemotron-3.5-content-safety:free", "cohere/north-mini-code:free",
            "someco/new-model-70b:free"}
    got = choose_models(live)
    assert got[:2] == ["google/gemma-4-31b-it:free", "qwen/qwen3.8-27b:free"], got
    assert "someco/new-model-70b:free" in got, "a new general model should be picked up"
    assert not any("safety" in m or "code" in m for m in got), got
    # The retired model is never tried just because it once worked.
    assert "meta-llama/llama-3.3-70b-instruct:free" not in choose_models(live)
    assert choose_models(None) == PREFERRED_FREE
    assert choose_models(live, "a/b:free, c/d") == ["a/b:free", "c/d"]
    print("llm self-check: ok")


if __name__ == "__main__":
    _demo()
