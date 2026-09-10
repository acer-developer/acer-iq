"""
WINNABILITY - can ACER realistically win this mandate?

ROADMAP_V3 Lever 3. The old fit score asked whether a company *needs* a rating.
Every CRA sees the same downgrade at the same hour, so need is not a
differentiator. This asks the narrower question: is this one **winnable**.

Four flags, all derived from data `fetch_credit_history` already returns - no new
network call:

  first_timer     no agency rates them, and the sources were reachable enough
                  to say so (`data_status == "none_found"`)
  inc_tagged      a CRA has flagged them Issuer Not Cooperating
  self_withdrawn  a rating was withdrawn at the issuer's own request
  multi_cra       already rated by 2+ agencies - a proven shopper

On top sits a CREDIT SCREEN, and it is not optional (see ROADMAP_V3 section 3).
INC-tagged and self-withdrawn issuers often left because they could not pay;
growing by importing another agency's defaults is negative value. A lead that
fails the screen is returned `blocked`, never suppressed silently.

Everything here is pure: dict in, dict out, no I/O. Run the self-check with

    python -m backend.pipeline.winnability
"""
from __future__ import annotations

import re

# Ratings at or below BB are speculative; D is default. An issuer sitting here
# while shopping for a new agency is the adverse-selection case.
_DISTRESS_RE = re.compile(
    r"\b(?:CRISIL|ICRA|CARE|IND|BWR|IVR|ACUITE)?\s*"
    r"(D|C|B\+|B-|B|BB\+|BB-|BB)\b(?!\w)", re.I)

_INC_RE = re.compile(
    r"issuer\s+not\s+cooperat|non[- ]cooperat|\bINC\b|"
    r"based\s+on\s+best\s+available\s+information", re.I)

_WITHDRAWN_RE = re.compile(r"withdraw", re.I)

# A withdrawal is only a buy signal when the *issuer* asked for it. A withdrawal
# forced by the agency (non-cooperation, no outstanding debt) is not.
_AT_REQUEST_RE = re.compile(
    r"at\s+the\s+(?:issuer|company|client)(?:'s|s)?\s+request|"
    r"at\s+(?:issuer|company)(?:'s|s)?\s+request|on\s+request\s+of\s+the", re.I)

# Weight per flag. Deliberately flat and readable - there is no outcome data yet
# to fit anything better, and pretending otherwise would dress opinion up as a
# model. ROADMAP_V3 phase 1 puts outcome logging in before this gets tuned.
_WEIGHTS = {
    "first_timer":    40,
    "inc_tagged":     30,
    "self_withdrawn": 25,
    "multi_cra":      20,
}

_REASONS = {
    "first_timer":    "No agency rates them - a first mandate, not a switch",
    "inc_tagged":     "Tagged Issuer Not Cooperating - the relationship has broken down",
    "self_withdrawn": "Withdrew a rating at their own request - actively shopping",
    "multi_cra":      "Already uses {n} agencies - proven to take a third quote",
}

SUPPRESS_BELOW = 40


def _action_text(credit_data: dict) -> str:
    """Every scrap of rating-action wording, lowercased into one haystack."""
    parts: list[str] = []
    for act in credit_data.get("rating_actions", []) or []:
        parts += [act.get("action", ""), act.get("rating", "")]
    for ag in credit_data.get("agencies", []) or []:
        for inst in ag.get("instruments", []) or []:
            parts += [inst.get("status", ""), inst.get("instrument_type", ""),
                      inst.get("rating", "")]
    return " ".join(p for p in parts if p)


def _worst_rating(credit_data: dict) -> str:
    """The lowest-looking rating we can see, for the credit screen."""
    for ag in credit_data.get("agencies", []) or []:
        for inst in ag.get("instruments", []) or []:
            m = _DISTRESS_RE.search(inst.get("rating", "") or "")
            if m:
                return m.group(0).strip()
    for act in credit_data.get("rating_actions", []) or []:
        m = _DISTRESS_RE.search(act.get("rating", "") or "")
        if m:
            return m.group(0).strip()
    return ""


def flags(credit_data: dict) -> dict:
    """The four winnability flags. Pure read over what credit_history returned."""
    rated_by = credit_data.get("rated_by_count", 0)
    status = credit_data.get("data_status", "unverified")
    text = _action_text(credit_data)

    withdrawn = bool(_WITHDRAWN_RE.search(text))
    return {
        # "nobody rates them" only counts when the sources actually answered.
        # On `unverified` the absence of ratings means nothing (credit_history.py).
        "first_timer":    rated_by == 0 and status == "none_found",
        "inc_tagged":     bool(_INC_RE.search(text)),
        "self_withdrawn": withdrawn and bool(_AT_REQUEST_RE.search(text)),
        "multi_cra":      rated_by >= 2,
    }


def credit_screen(credit_data: dict) -> dict:
    """Non-negotiable gate. Returns {"pass": bool, "reason": str}."""
    if credit_data.get("data_status") == "unverified":
        return {"pass": False,
                "reason": "Sources unreachable - creditworthiness unverified, do not call yet"}
    worst = _worst_rating(credit_data)
    if worst:
        return {"pass": False,
                "reason": f"Carries a speculative-grade rating ({worst}) - credit review before contact"}
    return {"pass": True, "reason": "No speculative-grade rating visible"}


def score(company: dict, credit_data: dict) -> dict:
    """
    Winnability verdict for one company.

    `blocked` means the credit screen failed: the lead is real but must not go to
    BD as-is. `suppressed` means it is simply not winnable enough to spend a call
    on. The two are different and the UI should say which.
    """
    f = flags(credit_data)
    rated_by = credit_data.get("rated_by_count", 0)

    raw = sum(w for k, w in _WEIGHTS.items() if f[k])
    value = min(raw, 100)

    reasons = []
    for k in _WEIGHTS:
        if f[k]:
            reasons.append(_REASONS[k].format(n=rated_by))
    if not reasons:
        reasons.append(
            "Comfortably rated elsewhere with no trigger - low priority"
            if rated_by else
            "No signal either way - rating coverage could not be established")

    screen = credit_screen(credit_data)
    return {
        "winnability": value,
        "label": _label(value),
        "flags": f,
        "reasons": reasons,
        "credit_screen": screen,
        "blocked": not screen["pass"],
        "suppressed": screen["pass"] and value < SUPPRESS_BELOW,
        "suppress_below": SUPPRESS_BELOW,
    }


def _label(value: int) -> str:
    if value >= 70: return "Highly winnable"
    if value >= 40: return "Worth a call"
    if value >= 20: return "Low priority"
    return "Not winnable"


# ── self-check ───────────────────────────────────────────────────────────────

def _demo() -> None:
    unrated = {"rated_by_count": 0, "data_status": "none_found",
               "agencies": [], "rating_actions": []}
    r = score({}, unrated)
    assert r["flags"]["first_timer"] and r["winnability"] == 40, r
    assert not r["suppressed"] and not r["blocked"], r

    # Same company, but the sources were down: absence proves nothing, and the
    # credit screen must refuse rather than wave it through.
    blind = dict(unrated, data_status="unverified")
    r = score({}, blind)
    assert not r["flags"]["first_timer"], r
    assert r["blocked"], r

    shopper = {
        "rated_by_count": 2, "data_status": "ok", "agencies": [],
        "rating_actions": [
            {"action": "Withdrawn at the issuer's request", "rating": "CRISIL A-"},
            {"action": "Reaffirmed", "rating": "ICRA A"},
        ],
    }
    r = score({}, shopper)
    assert r["flags"]["self_withdrawn"] and r["flags"]["multi_cra"], r
    assert r["winnability"] == 45 and r["label"] == "Worth a call", r

    # Agency-forced withdrawal is not a buy signal.
    forced = dict(shopper, rated_by_count=1,
                  rating_actions=[{"action": "Rating withdrawn due to non-cooperation",
                                   "rating": "CARE BBB"}])
    r = score({}, forced)
    assert not r["flags"]["self_withdrawn"], r
    assert r["flags"]["inc_tagged"], r

    # Distressed issuer: winnable on paper, blocked by the credit screen.
    distressed = {
        "rated_by_count": 1, "data_status": "ok",
        "agencies": [{"instruments": [{"rating": "CRISIL D", "status": "Downgraded"}]}],
        "rating_actions": [{"action": "Issuer not cooperating", "rating": "CRISIL D"}],
    }
    r = score({}, distressed)
    assert r["flags"]["inc_tagged"] and r["winnability"] >= 30, r
    assert r["blocked"], r

    happy = {"rated_by_count": 1, "data_status": "ok",
             "agencies": [{"instruments": [{"rating": "CRISIL AA", "status": "Reaffirmed"}]}],
             "rating_actions": []}
    r = score({}, happy)
    assert r["winnability"] == 0 and r["suppressed"] and not r["blocked"], r

    print("winnability self-check: ok")


if __name__ == "__main__":
    _demo()
