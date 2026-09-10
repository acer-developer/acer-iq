"""
LLM access for scoring and fit analysis, over OpenAI-compatible chat APIs.

Providers are tried in order and the first usable reply wins, so a dead key or
a rate-limited free tier degrades to the next provider instead of silently
dropping AI analysis:

  1. OpenRouter   - free models, e.g. meta-llama/llama-3.3-70b-instruct:free
  2. TokenRouter  - fallback, z-ai/glm-5.3-free

With no provider configured, chat() returns None and callers fall back to
rule-based scoring.
"""

import json
import logging

import httpx

from backend.config import settings

log = logging.getLogger("acer-iq.llm")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
FREE_MODEL     = "meta-llama/llama-3.3-70b-instruct:free"

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
            "model":      FREE_MODEL,
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


async def chat(prompt: str, max_tokens: int = 600) -> str | None:
    """Send a prompt to the first provider that answers. Returns None when no
    provider is configured or all of them fail."""
    providers = _providers()
    if not providers:
        return None

    async with httpx.AsyncClient(timeout=60) as client:
        for p in providers:
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
                    log.warning("%s error: %s", p["name"], data["error"])
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

                return text
            except Exception as e:
                log.warning("%s call failed: %s: %s", p["name"], type(e).__name__, e)

    log.warning("all %d LLM provider(s) failed", len(providers))
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
