"""
NEWS CLASSIFY - "major only", sector tags and the why-it-matters line.

BUILD_PLAN invariant 4: Tab 1 and Tab 3 show events with credit consequence.
Routine board approvals and market chatter are not news. Measured on one live
poll (28 Sep 2026): of 428 NSE filings the keyword tags let through, 111 were
"Outcome of Board Meeting" / bonus / dividend and 123 were "Acquisition" labels
that are mostly SAST shareholding disclosures - noise to a rating BD.

Every item gets:

  kind     what happened, in ACER's terms (rating_action, debt_raise,
           credit_stress, deal, capex, macro, equity_raise, results,
           board_routine, market_chatter, general)
  major    True only for kinds that can lead to a rating mandate or move a
           sector's credit - the default Market News view
  sectors  sector keys (SECTORS below), from the headline and company name
  macro    True for economy- or sector-wide events: the input to the Macro tab
  why      one plain sentence on why it matters to ACER, with the next action

Deterministic rules, no LLM: the same item always classifies the same way, so
a list can be re-run and explained months later, and nothing blanks when a
free LLM tier is down (PREMORTEM.md section 6).
ponytail: keyword rules miss phrasing they have not seen and cannot weigh
materiality beyond a stated rupee amount. Upgrade path is an LLM re-labeller
that may only *narrow* this output, never replace the rule-based `why`.

Self-check (no network):  python -m backend.pipeline.news_classify
"""
from __future__ import annotations

import re

# -- sectors -----------------------------------------------------------------
# key -> (label, pattern over headline + company name). Word-bounded and
# deliberately specific: a wrong sector tag puts the wrong names on the Macro
# tab, which is worse than no tag.
SECTORS: dict[str, tuple[str, str]] = {
    "nbfc":        ("NBFCs & housing finance",
                    r"\bNBFCs?\b|non[- ]banking|micro ?financ|\bMFIs?\b|gold loan|housing financ|\bHFCs?\b|"
                    r"\bfinserv\b|\bfinance (?:ltd|limited|company)\b|\bcapital (?:ltd|limited)\b|"
                    r"co-lending|risk weights?|\bleasing\b"),
    "banking":     ("Banks",
                    r"\bbanks?\b|\bbanking\b|\bPSBs?\b|\blenders?\b"),
    "real_estate": ("Real estate",
                    r"real estate|\brealty\b|housing project|\bRERA\b|\bproperties\b|\bdevelopers?\b|"
                    r"residential project|commercial real|home ?buyers?"),
    "infra":       ("Infrastructure & EPC",
                    r"\binfra(?:structure)?\b|\bhighways?\b|\broads?\b|\bEPC\b|\bports?\b|\bairports?\b|"
                    r"\bmetro\b|\brailways?\b|\bconstruction\b|\bprojects (?:ltd|limited)\b|\bNHAI\b"),
    "power":       ("Power & renewables",
                    r"\bpower\b|electricity|renewable|\bsolar\b|\bwind\b|\bdiscoms?\b|\bPPAs?\b|"
                    r"transmission|green energy|\benergy\b"),
    "metals":      ("Metals & mining",
                    r"\bsteel\b|iron ore|aluminium|\bcopper\b|\bzinc\b|\bmetals?\b|\bmining\b|\bcoal\b"),
    "oil_gas":     ("Oil & gas",
                    r"\bcrude\b|\boil\b|natural gas|\bgas\b|petroleum|(?<!gold )refiner(?:y|ies)|\bLNG\b|\bfuel\b"),
    "auto":        ("Auto & EV",
                    r"\bauto(?:mobile|motive)?s?\b|\bvehicles?\b|\bEVs?\b|electric (?:bus|car|two)|"
                    r"two[- ]wheeler|tractors?|\bmotors\b"),
    "pharma":      ("Pharma & healthcare",
                    r"\bpharma|\bdrugs?\b|hospitals?|healthcare|diagnostic|laborator(?:y|ies)|\bUSFDA\b|\bANDA\b"),
    "chemicals":   ("Chemicals & fertilisers",
                    r"chemicals?|fertili[sz]ers?|agrochem|\bspecialty chem"),
    "textiles":    ("Textiles",
                    r"textiles?|apparel|\bcotton\b|\byarn\b|garments?"),
    "cement":      ("Cement & building materials",
                    r"\bcement\b|building materials|\btiles\b"),
    "logistics":   ("Aviation, shipping & logistics",
                    r"airlines?|aviation|logistics|shipping|freight"),
    "agri":        ("Agri & food",
                    r"monsoon|\bkharif\b|\brabi\b|\bagri|\bfarm|\bsugar\b|\brice\b|\bwheat\b|\bMSP\b"),
}
_SECTOR_RE = {k: re.compile(p, re.I) for k, (_, p) in SECTORS.items()}

# Macro drivers that hit sectors the headline does not name. Crude at $100 is
# an airline and chemicals story even when the article only says "oil".
_KNOCK_ON: list[tuple[re.Pattern, tuple[str, ...]]] = [
    (re.compile(r"repo rate|rate (?:hike|cut)|monetary policy|\bMPC\b|liquidity|\bCRR\b|\bSLR\b|"
                r"bond yields?|10-year yield|g-?sec|govt borrowing|government borrowing", re.I),
     ("nbfc", "banking", "real_estate")),
    (re.compile(r"\bcrude\b|oil (?:price|tops|surge|jumps|at \$)|brent", re.I),
     ("oil_gas", "logistics", "chemicals")),
    (re.compile(r"rupee (?:slips|falls|weakens|hits|record low)|rupee.*\bvs\b.*dollar", re.I),
     ("oil_gas", "chemicals")),
    (re.compile(r"monsoon|rainfall deficit|drought", re.I), ("agri", "nbfc")),
]

# -- kinds -------------------------------------------------------------------
_P = lambda p: re.compile(p, re.I)   # noqa: E731

_CHATTER = _P(r"stocks? to (?:buy|sell|watch)|target price|stop[- ]loss|\bnifty\b|\bsensex\b|"
              r"52[- ]week|market wrap|top gainers|stock market (?:today|prediction|crash)|"
              r"\bGMP\b|breakout stocks|shares? (?:rise|fall|jump|slip|surge|dip|plunge|rall)|"
              r"stock (?:rallies|surges|falls|slides|plunges|slips)|upper circuit|multibagger|"
              r"quote of the day|\bbitcoin\b|ahead of market|things that will decide|"
              r"stocks? in focus|(?:stocks|shares) to watch|global market|european shares|"
              r"asian (?:shares|markets)|wall street|\bus (?:stocks|market)\b|dow jones|nasdaq")
_ROUTINE = _P(r"outcome of board meeting|board meeting intimation|\bbonus\b|\bdividend\b|record date|"
              r"\bAGM\b|annual general meeting|trading window|appoint(?:s|ed|ment)|resign|"
              r"change in (?:director|management|kmp)|newspaper publication|investor (?:meet|presentation)|"
              r"analysts?/institutional|loss of share certificate|closure of trading")
# Servicing existing debt, not raising new debt: an NCD's record date, interest
# payment, redemption or the exchange suspending it at maturity. Found on live
# data 2026-09-28 ("Suspension of Trading" read as a debt raise).
_DEBT_SERVICING = _P(r"suspension of trading|redemption|record date|payment of interest|"
                     r"interest payment|intimation of (?:interest|principal)|"
                     r"due for (?:payment|redemption)|servicing of")
_SAST = _P(r"takeover regulations|\bSAST\b|regulation 29|regulation 31|disclosure under sebi")
_RATING = _P(r"credit rating|\brating\b.*\b(?:assign|upgrad|downgrad|reaffirm|revis|withdraw|"
             r"outlook)|\b(?:CRISIL|ICRA|CARE Ratings|India Ratings|Acuit[eé]|Brickwork|Infomerics)\b")
# NCLT alone is not stress: it also sanctions every merger scheme.
_STRESS = _P(r"\bdefault(?:s|ed)?\b|insolvency|\bIBC\b|\bCIRP\b|delay(?:ed)? in (?:payment|servicing)|"
             r"moratorium|restructur|\bdowngrad|\bfraud\b|wilful defaulter|\bNPA\b|"
             r"issuer not cooperating|liquidation")
_DEBT = _P(r"\bNCDs?\b|non[- ]convertible debentures?|\bdebentures?\b|\bbonds?\b|commercial paper|"
           r"\bCPs\b|securiti[sz]ation|\bECB\b|term loan|borrow(?:ing|s)|debt (?:raise|issue|fund)|"
           r"green (?:bonds?|debt)|blue bond|masala bond")
_EQUITY = _P(r"\bIPO\b|\bQIP\b|qualified institutional|rights issue|preferential (?:issue|allotment)|"
             r"\bDRHP\b|draft (?:red herring|papers)|\bFPO\b|offer for sale|\bOFS\b|series [a-e]\b|"
             r"raises? .*(?:funding|from investors)")
_DEAL = _P(r"\bacquir(?:e|es|ed|ing)\b|acquisition of|\bmerger\b|amalgamat|\btakeover\b|"
           r"slump sale|demerger|\bstake (?:sale|in)\b|joint venture|\bJV\b|scheme of arrangement")
_CAPEX = _P(r"\bcapex\b|capital expenditure|capacity (?:addition|expansion|augment)|greenfield|"
            r"brownfield|new (?:plant|facility|unit)|commissions?\b|expansion|\bproject\b")
_MACRO = _P(r"\bRBI\b|repo rate|monetary policy|\bMPC\b|inflation|\bCPI\b|\bWPI\b|\bGDP\b|"
            r"fiscal deficit|union budget|\bGST\b|tariffs?|import duty|export duty|anti-dumping|"
            r"\bSEBI\b.*(?:norms|rules|framework|circular)|\bIRDAI\b|ministry|government (?:approves|notifies)|"
            r"\bpolicy\b|regulat(?:or|ion|ory)|bond yields?|10-year yield|crude|rupee|monsoon|"
            r"risk weights?|liquidity|govt borrowing|government borrowing")
_RESULTS = _P(r"\bQ[1-4]\b.*(?:profit|loss|revenue|results)|net (?:profit|loss)|quarterly results|"
              r"financial results|\bPAT\b|\bEBITDA\b")

# A named company doing something to itself: "X raises", "X plans", "X to
# issue". Keeps "Reliance plans Rs 10,000 crore securitisation" a debt raise
# even though "borrowing" also reads as macro.
_COMPANY_ACTION = _P(r"\b(?:raises?|plans?|to (?:raise|issue)|issues?|allots?|files?|inks?|bags?|"
                     r"withdraws?|acquires?|commissions?)\b")

# Rs / INR / rupee-sign amounts in crore, and "lakh crore". Used only as a
# materiality floor for deals and capex; never displayed as a figure.
_AMOUNT = _P(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(lakh crore|crore|cr\b)")
_BILLION = _P(r"\$\s*[\d.]+\s*(?:billion|bn)\b")
MATERIAL_CRORE = 500


def _amount_crore(text: str) -> float | None:
    best = None
    for num, unit in _AMOUNT.findall(text):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        v = v * 100000 if unit.lower().startswith("lakh") else v
        best = v if best is None else max(best, v)
    if best is None and _BILLION.search(text):
        best = 8000.0   # any US$ billion clears the floor several times over
    return best


def sectors_for(text: str) -> list[str]:
    """Sector keys named in `text`, plus knock-on sectors of macro drivers."""
    found = [k for k, rx in _SECTOR_RE.items() if rx.search(text)]
    for rx, keys in _KNOCK_ON:
        if rx.search(text):
            found += [k for k in keys if k not in found]
    return found


def sector_label(key: str) -> str:
    return SECTORS.get(key, (key, ""))[0]


def _kind(item: dict, text: str) -> str:
    subject = item.get("subject", "") or ""
    is_nse = item.get("source") == "NSE"
    # Order matters: the more consequential reading wins - except that a
    # stock-tips headline naming "Crisil" as a ticker is still chatter.
    if _CHATTER.search(subject) and not _STRESS.search(subject):
        return "market_chatter"
    if _STRESS.search(text):
        return "credit_stress"
    if _RATING.search(text):
        return "rating_action"
    # A headline with no company behind it that speaks of the RBI, yields or
    # government borrowing is the economy, not one issuer's debt raise.
    if not is_nse and not item.get("company") and _MACRO.search(subject) \
            and not _COMPANY_ACTION.search(subject):
        return "macro"
    if is_nse and _DEBT_SERVICING.search(subject):
        return "board_routine"          # paying existing debt, not raising new
    if _DEBT.search(text) and not _EQUITY.search(subject):
        return "debt_raise"
    if is_nse and _SAST.search(text):
        return "board_routine"          # shareholding disclosure, not a deal
    if _DEAL.search(text):
        return "deal"
    if _EQUITY.search(text):
        return "equity_raise"
    if not is_nse and not item.get("company") and _MACRO.search(subject):
        return "macro"
    if _CAPEX.search(text) and (_amount_crore(text) or 0) >= MATERIAL_CRORE:
        return "capex"
    if _ROUTINE.search(text):
        return "board_routine"
    if _RESULTS.search(text):
        return "results"
    if _MACRO.search(subject):
        return "macro"
    return "general"


def _is_major(kind: str, text: str, sectors: list[str]) -> bool:
    if kind in ("rating_action", "debt_raise", "credit_stress", "capex"):
        return True
    if kind == "deal":
        # A deal is material when it states a size over the floor, or is a
        # whole-company combination. A 2% stake purchase is not.
        amt = _amount_crore(text)
        return (amt or 0) >= MATERIAL_CRORE or bool(
            re.search(r"\bmerger\b|amalgamat|takeover|acquisition of (?:the )?(?:entire|100|majority)", text, re.I))
    if kind == "macro":
        return bool(sectors)            # sector-level consequence, or it is chatter
    return False


# "Clean Max Enviro Energy raises ..." -> "Clean Max Enviro Energy". Only a
# run of capitalised words directly before a company verb counts; anything
# less certain falls back to "The issuer" rather than naming the wrong firm.
_HEADLINE_CO = re.compile(
    r"^((?:[A-Z][\w&.'-]*|[0-9][\w&-]*)(?: (?:[A-Z][\w&.'-]*|[0-9][\w&-]*|of|and|&)){0,5}) "
    r"(?:raises|plans|inks|to (?:develop|raise|issue|acquire|buy)|withdraws|commissions|"
    r"board approves|bags|files|acquires|secures|moots|signs|launches|allots)\b")


def _who(item: dict) -> str:
    name = (item.get("company") or "").strip()
    if name:
        return name
    m = _HEADLINE_CO.match((item.get("subject") or "").strip())
    return m.group(1) if m else "The issuer"


def _why(kind: str, item: dict, sectors: list[str], text: str) -> str:
    who = _who(item)
    labels = ", ".join(sector_label(s) for s in sectors[:3])
    amt = _amount_crore(text)
    size = f" (about Rs {amt:,.0f} crore)" if amt and amt < 8000 else ""
    if kind == "rating_action":
        return (f"{who} just had a rating action at another agency - the moment an issuer "
                "compares agencies; read the rationale and offer ACER as a second rating.")
    if kind == "debt_raise":
        return (f"{who} is raising debt{size} - NCDs, bonds and CP need a rating before issue; "
                "reach them before the mandate is placed.")
    if kind == "credit_stress":
        return (f"{who} shows credit stress - do not pitch as-is; a downgrade or INC tag here often "
                "sends the issuer shopping for a new agency, so watch and screen before contact.")
    if kind == "capex":
        return (f"{who} announced capex{size} - project funding means new bank facilities that "
                "need a bank loan rating.")
    if kind == "deal":
        return (f"{who} is doing a deal{size} - acquisition funding usually brings new borrowing "
                "that needs rating.")
    if kind == "macro":
        return (f"Hits {labels}: borrowers there may need fresh funding or face rating pressure "
                "- the Macro tab lists the named companies." if labels else
                "Economy-wide move with no sector we can map - context only.")
    if kind == "equity_raise":
        return "Equity raise - no rating needed for the issue itself; context only."
    if kind == "results":
        return "Quarterly numbers - context for a pitch, not a trigger on their own."
    if kind == "board_routine":
        return "Routine corporate filing - no credit consequence."
    if kind == "market_chatter":
        return "Market commentary - no credit consequence."
    return "No credit trigger identified - context only."


def classify(item: dict) -> dict:
    """{kind, major, sectors, macro, why} for one archived news item."""
    head = " ".join(filter(None, (item.get("subject"), item.get("company"))))
    # An NSE subject is a one-word label, so the filing summary is the text.
    # A news headline is the claim; its teaser paragraph mentions bonds,
    # stakes and projects in passing and would drag noise in.
    text = (" ".join(filter(None, (head, item.get("description"))))
            if item.get("source") == "NSE" else head)
    kind = _kind(item, text)
    # Sectors come from the headline + company only: descriptions of long
    # filings mention every industry under the sun.
    sectors = sectors_for(head)
    major = _is_major(kind, text, sectors)
    return {"kind": kind, "major": major, "sectors": sectors,
            "sector_labels": [sector_label(s) for s in sectors],
            "macro": kind == "macro" and major,
            "why": _why(kind, item, sectors, text)}


# -- self-check ----------------------------------------------------------------

def _demo() -> None:
    def c(subject, source="Economic Times", company="", description=""):
        return classify({"subject": subject, "source": source, "company": company,
                         "description": description})

    # Routine filings and chatter are not news.
    assert not c("Outcome of Board Meeting", "NSE", "Prime Focus Limited")["major"]
    assert not c("Bonus", "NSE", "Concord Biotech Limited")["major"]
    assert c("Top 5 Breakout stocks to buy: KPI Green, Crisil, Manyavar")["kind"] == "market_chatter"
    assert not c("Nifty slides to six-month low as oil tops $100 a barrel")["major"]
    sast = c("Disclosure under SEBI Takeover Regulations", "NSE", "Capillary Technologies")
    assert not sast["major"], sast

    # Rating actions, debt raises and stress are.
    r = c("Credit Rating", "NSE", "Capri Global Capital Limited")
    assert r["kind"] == "rating_action" and r["major"], r
    assert "nbfc" in r["sectors"], r
    d = c("Clean Max Enviro Energy raises Rs 2,500 cr via green bonds")
    assert d["kind"] == "debt_raise" and d["major"] and "2,500" in d["why"], d
    assert d["why"].startswith("Clean Max Enviro Energy is raising debt"), d["why"]
    assert c("Reliance plans ₹10,000 crore securitisation deal in borrowing push")["kind"] == "debt_raise"
    s = c("Lender moves NCLT against Acme Infra for insolvency")
    assert s["kind"] == "credit_stress" and s["major"] and "do not pitch" in s["why"], s

    # Capex needs a stated size over the floor.
    cap = c("NMDC commissions ₹5,427 crore iron ore processing complex in Chhattisgarh")
    assert cap["kind"] == "capex" and cap["major"] and "metals" in cap["sectors"], cap
    assert not c("Visa-owned Pismo plans expansion in India")["major"]

    # Equity is context, not a rating trigger.
    ipo = c("20 IPOs to open for subscription this week")
    assert not ipo["major"], ipo

    # Macro: sector-level consequence, with knock-on sectors.
    m = c("RBI may shift govt borrowing towards shorter tenures in H2")
    assert m["kind"] == "macro" and m["macro"] and "nbfc" in m["sectors"], m
    oil = c("Rupee slips toward 96 vs US dollar as crude, yields weigh")
    assert oil["macro"] and "oil_gas" in oil["sectors"], oil

    # Deals: size or whole-company, not any stake.
    assert c("Paramount rolls out $44 billion bond sale to fund Warner Bros. takeover")["major"]
    assert c("Noel Tata moots merger of 2 group firms with Tata Sons")["major"]

    # Found on live data (28 Sep 2026): NCLT sanctioning a scheme is a deal,
    # not stress; "stocks in focus" and global wraps are chatter.
    dab = c("Scheme of Arrangement", "NSE", "Dabur India Limited",
            "Dabur India Limited has informed the Exchange about order passed by the NCLT")
    assert dab["kind"] == "deal", dab
    assert c("Top stocks in focus tomorrow: Investors must watch HCL Tech, NCC")["kind"] == "market_chatter"
    assert c("Global Market: European shares edge higher as housebuilders rally",
             description="bond yields fell")["kind"] == "market_chatter"

    # Servicing existing NCDs is routine, not a debt raise.
    sus = c("Suspension of Trading", "NSE", "Aditya Birla Capital Limited",
            "suspension of trading of NCDs on account of redemption")
    assert sus["kind"] == "board_routine" and not sus["major"], sus
    assert c("Allotment of Securities", "NSE", "IIFL Finance Limited",
             "allotment of Non-Convertible Debentures")["kind"] == "debt_raise"

    # Every item has a reason; nothing blanks.
    for subj in ("", "Quote of the day by Howard Marks", "Truecaller launches scam checker"):
        assert c(subj)["why"], subj
    print("news_classify self-check: ok")


if __name__ == "__main__":
    _demo()
