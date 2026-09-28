"""The BD roster (backend/data/bd_roster.json): who the BDs are, which
segment each owns, and - optionally - the email each signs in with, which
is what lets a lead be owned by the BD it is assigned to."""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("acer-iq.roster")

PATH = Path(__file__).parent.parent / "data" / "bd_roster.json"


def roster() -> list[dict]:
    """The BDs, in draft order."""
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return [b for b in data if b.get("id") and b.get("name")]
    except Exception as e:
        log.error("bd_roster.json unreadable: %s", e)
        return []


def profile_for_email(email: str) -> str:
    email = (email or "").strip().lower()
    return next((b["id"] for b in roster() if email and (b.get("email") or "").lower() == email), "")


def email_for_profile(bd_id: str) -> str:
    return next(((b.get("email") or "").lower() for b in roster() if b["id"] == bd_id), "")
