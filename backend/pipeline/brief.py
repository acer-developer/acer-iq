"""
PITCH BRIEF - one page per company, for the meeting.

ROADMAP_V3 phase 4. Renders print-ready HTML rather than a PDF binary: the
browser's own print-to-PDF produces the file, so there is no PDF library, no
font bundling and no server-side layout engine to go stale. `@page A4` plus a
page break per company means one company is one sheet, which is the whole
requirement. Ctrl+P, Save as PDF.

It states only what is stored. Nothing is re-fetched or re-scored at print time,
because a brief that quietly disagrees with the pipeline it was printed from is
worse than no brief: the flags shown are the flags as they stood when the lead
was saved, and the footer says so.

Self-check (no network):  python -m backend.pipeline.brief
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from backend.pipeline import winnability

_CSS = """
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body { font: 11pt/1.45 Georgia, 'Times New Roman', serif; color: #111; margin: 0; }
.sheet { page-break-after: always; }
.sheet:last-child { page-break-after: auto; }
h1 { font-size: 19pt; margin: 0 0 2px; }
h2 { font-size: 10pt; letter-spacing: .09em; text-transform: uppercase;
     color: #666; margin: 18px 0 6px; border-bottom: 1px solid #ddd;
     padding-bottom: 3px; font-family: Helvetica, Arial, sans-serif; }
.meta { font: 9.5pt Helvetica, Arial, sans-serif; color: #555; }
.score { float: right; text-align: center; border: 2px solid #111;
         padding: 6px 12px; font-family: Helvetica, Arial, sans-serif; }
.score b { display: block; font-size: 20pt; line-height: 1; }
.score span { font-size: 7.5pt; text-transform: uppercase; letter-spacing: .08em; }
ul { margin: 4px 0; padding-left: 18px; }
li { margin: 3px 0; }
table { width: 100%; border-collapse: collapse; font-size: 10pt; }
td { padding: 3px 0; vertical-align: top; }
td.k { width: 34%; color: #666; font-family: Helvetica, Arial, sans-serif;
       font-size: 9pt; }
.chip { display: inline-block; border: 1px solid #999; border-radius: 9px;
        padding: 1px 8px; margin: 2px 4px 2px 0;
        font: 8.5pt Helvetica, Arial, sans-serif; }
.weak { color: #777; font-size: 8.5pt; }
.foot { margin-top: 20px; border-top: 1px solid #ddd; padding-top: 6px;
        font: 8.5pt Helvetica, Arial, sans-serif; color: #777; }
@media screen { body { background: #f4f4f4; }
  .sheet { background: #fff; max-width: 190mm; margin: 14px auto;
           padding: 16mm 14mm; box-shadow: 0 1px 4px rgba(0,0,0,.18); } }
"""

_FLAG_LABEL = {
    "first_timer": "First-timer",
    "inc_tagged": "Issuer Not Cooperating",
    "self_withdrawn": "Self-withdrawn rating",
    "multi_cra": "Rated by 2+ agencies",
}


def _e(value) -> str:
    """Escape anything bound for the page. Every name on this sheet came out of
    a scraper, so it is untrusted text going into HTML."""
    return escape(str(value if value is not None else ""), quote=True)


def _date(raw: str) -> str:
    return (raw or "")[:10]


def _sheet(brief: dict) -> str:
    lead = brief["lead"]
    flags = lead.get("flags") or {}
    active = [label for key, label in _FLAG_LABEL.items() if flags.get(key)]
    reasons = winnability.reasons_for(flags, len(lead.get("agencies") or []))
    events = brief.get("events") or []
    news = brief.get("news") or []

    chips = "".join(f'<span class="chip">{_e(c)}</span>' for c in active) or (
        '<span class="weak">No winnability signal was recorded against this lead.</span>')
    reason_items = "".join(f"<li>{_e(r)}</li>" for r in reasons)

    rows = [("Stage", lead.get("stage", "")),
            ("Agencies seen", ", ".join(lead.get("agencies") or []) or "-"),
            ("CIN", lead.get("cin") or "not resolved"),
            ("Saved", _date(lead.get("saved_at", ""))),
            ("Last updated", _date(lead.get("updated_at", "")))]
    row_html = "".join(
        f'<tr><td class="k">{_e(k)}</td><td>{_e(v)}</td></tr>' for k, v in rows)

    if news:
        news_html = "<ul>" + "".join(
            f'<li>{_e(n.get("subject"))}'
            f'<div class="meta">{_e(n.get("source"))} - {_e(_date(n.get("date", "")))}'
            + ('  <span class="weak">(weak name match, verify)</span>'
               if n.get("confidence") == "weak" else "")
            + "</div></li>" for n in news) + "</ul>"
    else:
        news_html = ('<p class="weak">No coverage found in the business papers '
                     'read. That is usually a quiet week, not a quiet company.</p>')

    if events:
        hist_html = "<ul>" + "".join(
            f'<li>{_e(ev.get("event"))}'
            + (f' - {_e(ev.get("detail"))}' if ev.get("detail") else "")
            + f' <span class="meta">{_e(_date(ev.get("at", "")))}</span></li>'
            for ev in events[:12]) + "</ul>"
    else:
        hist_html = '<p class="weak">No activity logged yet.</p>'

    notes = (f'<h2>Notes</h2><p>{_e(lead.get("notes"))}</p>'
             if lead.get("notes") else "")
    score = lead.get("winnability")

    return f"""
<div class="sheet">
  <div class="score"><b>{_e(score if score is not None else "-")}</b>
    <span>Winnability</span></div>
  <h1>{_e(lead.get("company_name"))}</h1>
  <p class="meta">ACER Ratings - pitch brief - printed
    {_e(datetime.now(timezone.utc).strftime("%d %b %Y"))}</p>

  <h2>Why this is winnable</h2>
  <div>{chips}</div>
  <ul>{reason_items}</ul>

  <h2>Where it stands</h2>
  <table>{row_html}</table>
  {notes}

  <h2>Recent coverage</h2>
  {news_html}

  <h2>Our history with this lead</h2>
  {hist_html}

  <p class="foot">Signals are as recorded when the lead was saved, not
  re-checked at print time. Verify the current rating position against the
  agency's own disclosure before quoting it in a meeting.</p>
</div>"""


def render(briefs: list[dict], title: str = "ACER-IQ pitch briefs") -> str:
    """One print-ready page per brief. `briefs` is
    [{"lead": <saved lead>, "events": [...], "news": [...]}, ...]."""
    body = "".join(_sheet(b) for b in briefs) or (
        '<div class="sheet"><h1>Nothing to brief</h1>'
        '<p class="weak">No leads matched. Add leads to the pipeline first.</p></div>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{_e(title)}</title><style>{_CSS}</style></head>'
            f'<body>{body}</body></html>')


# -- self-check (no network) -------------------------------------------------

def _demo() -> None:
    html = render([{
        "lead": {"company_name": "Berar <script>alert(1)</script> Ltd",
                 "stage": "Contacted", "winnability": 70, "cin": None,
                 "flags": {"inc_tagged": True, "multi_cra": True},
                 "agencies": ["CARE", "INDRA"], "notes": "CFO asked for a quote",
                 "saved_at": "2026-09-11T10:00:00+00:00",
                 "updated_at": "2026-09-14T10:00:00+00:00"},
        "events": [{"event": "stage", "detail": "Identified -> Contacted",
                    "at": "2026-09-14T10:00:00+00:00"}],
        "news": [{"subject": "Berar raises debt", "source": "LiveMint",
                  "date": "2026-09-13", "confidence": "weak"}],
    }])

    # Scraped names go into HTML: they must be escaped, always.
    assert "<script>" not in html and "&lt;script&gt;" in html, "unescaped name"
    # The stored flags drive the pitch, in winnability's own wording.
    assert "Issuer Not Cooperating" in html
    assert "the relationship has broken down" in html
    assert "Rated by 2+ agencies" in html
    # A weak name match must be visible as one, or BD quotes someone else's news.
    assert "weak name match" in html
    # One company, one sheet.
    assert html.count('class="sheet"') == 1
    assert "page-break-after: always" in html
    # An empty pipeline prints a page that says so, not a blank file.
    assert "Nothing to brief" in render([])

    print("brief self-check: ok")


if __name__ == "__main__":
    _demo()
