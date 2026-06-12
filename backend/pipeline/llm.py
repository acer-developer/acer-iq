"""
Thin wrapper around OpenRouter's free-tier chat completions.
Falls back gracefully when no key is configured.

Free models that work well for structured JSON output:
  - meta-llama/llama-3.3-70b-instruct:free   (best quality, recommended)
  - google/gemma-3-27b-it:free
  - mistralai/mistral-7b-instruct:free        (fastest)
  - deepseek/deepseek-r1:free                 (reasoning, slower)
"""

import hashlib
import json
import logging

import httpx

from backend.config import settings
from backend.netutil import SourceStatus, cache, limiter

log = logging.getLogger("acer_iq.llm")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
FREE_MODEL     = "meta-llama/llama-3.3-70b-instruct:free"
CACHE_TTL      = 6 * 3600  # same prompt is never sent twice within 6h


async def chat(prompt: str, max_tokens: int = 600,
               status: SourceStatus | None = None) -> str | None:
    """
    Send a prompt to OpenRouter and return the raw text response.
    Returns None if no API key is set or if the call fails.
    Cached by prompt hash; concurrency-capped via the 'llm' limiter.
    """
    api_key = settings.openrouter_api_key
    if not api_key or api_key in ("your_key_here", ""):
        if status:
            status.skip("llm", "no OpenRouter API key configured")
        return None

    key = "llm:" + hashlib.sha256(f"{max_tokens}:{prompt}".encode()).hexdigest()
    hit = cache.get(key)
    if hit is not None:
        if status:
            status.ok("llm")
        return hit

    try:
        async with limiter("llm"):
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type":  "application/json",
                        "HTTP-Referer":  "https://acer-iq.app",
                        "X-Title":       "ACER-IQ",
                    },
                    json={
                        "model":      FREE_MODEL,
                        "messages":   [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                    },
                )
        data = resp.json()

        if "error" in data:
            detail = str(data["error"].get("message", data["error"]))[:200]
            log.warning("OpenRouter error: %s", detail)
            if status:
                status.fail("llm", detail)
            return None

        text = data["choices"][0]["message"]["content"].strip()
        cache.set_result(key, text, CACHE_TTL)
        if status:
            status.ok("llm")
        return text
    except Exception as exc:
        log.warning("LLM call failed: %r", exc)
        if status:
            status.fail("llm", repr(exc))
        return None


def parse_json(raw: str) -> dict | list | None:
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
        # Find the embedded JSON value — whichever bracket opens first wins,
        # so a top-level array isn't truncated to its first object
        pairs = [("{", "}"), ("[", "]")]
        pairs.sort(key=lambda p: text.find(p[0]) if text.find(p[0]) != -1 else len(text))
        for open_ch, close_ch in pairs:
            start = text.find(open_ch)
            end   = text.rfind(close_ch) + 1
            if start != -1 and end > start:
                try:
                    return json.loads(text[start:end])
                except Exception:
                    continue
    return None
