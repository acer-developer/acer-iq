"""
Thin wrapper around OpenRouter's free-tier chat completions.
Falls back gracefully when no key is configured.

Free models that work well for structured JSON output:
  - meta-llama/llama-3.3-70b-instruct:free   (best quality, recommended)
  - google/gemma-3-27b-it:free
  - mistralai/mistral-7b-instruct:free        (fastest)
  - deepseek/deepseek-r1:free                 (reasoning, slower)
"""

import json
import httpx
from backend.config import settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
FREE_MODEL     = "meta-llama/llama-3.3-70b-instruct:free"


async def chat(prompt: str, max_tokens: int = 600) -> str | None:
    """
    Send a prompt to OpenRouter and return the raw text response.
    Returns None if no API key is set or if the call fails.
    """
    api_key = settings.openrouter_api_key
    if not api_key or api_key in ("your_key_here", ""):
        return None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                    "HTTP-Referer":  "https://credsight.app",
                    "X-Title":       "CredSight",
                },
                json={
                    "model":      FREE_MODEL,
                    "messages":   [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                },
            )
            data = resp.json()

            # OpenRouter error handling
            if "error" in data:
                return None

            return data["choices"][0]["message"]["content"].strip()
    except Exception:
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
