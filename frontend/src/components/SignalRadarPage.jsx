import React, { useState, useEffect, useCallback } from "react";
import { apiUrl } from "../lib/api.js";

const PRIORITY_CATEGORIES = ["fund_raise", "rating_action", "expansion"];

const CATEGORY_META = {
  fund_raise:    { label: "Fund Raise",    color: "bg-blue-100 text-blue-700 border-blue-200" },
  rating_action: { label: "Rating Action", color: "bg-amber-100 text-amber-700 border-amber-200" },
  expansion:     { label: "Expansion",     color: "bg-green-100 text-green-700 border-green-200" },
};

const PLAYBOOKS = [
  {
    id: "growing",
    icon: "M13 7h8m0 0v8m0-8l-8 8-4-4-6 6",
    color: "emerald",
    situation: "Sector on the rise",
    when: "When a sector is expanding: renewables, defense, EMS, real estate cycles up.",
    why: "Companies raise fresh debt for capex. NCDs, bonds, and bank loans surge. Debut issuers appear.",
    play: "Chase debut issuers before Big 3 lock them in. Offer speed and price on first mandates. Win at debut, keep through surveillance.",
    signal: "In Market News, filter by Fund Raise + Expansion. Look for first-time NCD board approvals.",
    matchesCategories: ["fund_raise", "expansion"],
  },
  {
    id: "declining",
    icon: "M13 17h8m0 0V9m0 8l-8-8-4 4-6-6",
    color: "rose",
    situation: "Sector under stress",
    when: "When a sector faces headwinds: MFI collapse, real estate stress, NBFC liquidity crunch.",
    why: "Existing CRAs downgrade or withdraw ratings. Companies get pushed off preferred lists. Rating shopping intensifies.",
    play: "Approach companies within 30 days of a rating withdrawal or downgrade by CRISIL/ICRA/CARE. They are forced to re-rate within 90 days by regulation.",
    signal: "In Market News, filter by Rating Action. Cross-check with the CRA's own website for the type of action.",
    matchesCategories: ["rating_action"],
  },
  {
    id: "stable",
    icon: "M5 12h14",
    color: "gray",
    situation: "Stable sector",
    when: "When a sector is neither expanding nor stressed: FMCG, mature manufacturing, utilities.",
    why: "Little new debt, but annual surveillance renewals dominate. Companies are fee-sensitive on renewal.",
    play: "Target incumbents 30 days before their annual surveillance expiry. Offer a 15 to 20 percent discount on surveillance to displace the incumbent CRA.",
    signal: "Requires the surveillance calendar (coming in V2). For now, use Company Research to check renewal timing.",
    matchesCategories: [],
  },
  {
    id: "first_time",
    icon: "M12 4v16m8-8H4",
    color: "blue",
    situation: "First-time issuers",
    when: "When a company issues debt for the first time: newer NBFCs, IPO-stage companies, mid-tier corporates.",
    why: "No incumbent CRA relationship to displace. Zero switching cost. Highest conversion rate.",
    play: "Target companies filing board approvals for their first NCD or first commercial paper. Bank BLR mandates for first-time borrowers are equally valuable.",
    signal: "In Market News, filter by Board Approval + Fund Raise. Company Research shows whether they have been rated before.",
    matchesCategories: ["fund_raise", "board_approval"],
  },
];

const COMING_SOON = [
  {
    title: "Rating Withdrawal Monitor",
    detail: "Weekly scrape of all 7 CRA websites for withdrawn and downgraded ratings. RBI mandates re-rating within 90 days.",
    eta: "Weeks 3 to 4",
  },
  {
    title: "Surveillance Calendar",
    detail: "Extract next surveillance date from CRA rating rationale PDFs. Shows which companies come up for renewal each week.",
    eta: "Weeks 5 to 6",
  },
  {
    title: "Sector Momentum Score",
    detail: "Aggregate signals by sector to show which are hot (high fund raising) vs stressed (high rating downgrades).",
    eta: "Weeks 5 to 6",
  },
  {
    title: "BLR Expiration Tracker",
    detail: "Bank loan ratings expire annually. Track expiries across the RBI accredited borrower base.",
    eta: "Weeks 7 to 8",
  },
];

const colorClasses = {
  emerald: {
    border: "border-emerald-200",
    bg: "bg-emerald-50",
    icon: "bg-emerald-100 text-emerald-700",
    tag: "bg-emerald-100 text-emerald-800",
  },
  rose: {
    border: "border-rose-200",
    bg: "bg-rose-50",
    icon: "bg-rose-100 text-rose-700",
    tag: "bg-rose-100 text-rose-800",
  },
  gray: {
    border: "border-gray-200",
    bg: "bg-gray-50",
    icon: "bg-gray-100 text-gray-700",
    tag: "bg-gray-100 text-gray-800",
  },
  blue: {
    border: "border-blue-200",
    bg: "bg-blue-50",
    icon: "bg-blue-100 text-blue-700",
    tag: "bg-blue-100 text-blue-800",
  },
};

function CategoryTag({ cat }) {
  const meta = CATEGORY_META[cat] || { label: cat, color: "bg-gray-100 text-gray-600 border-gray-200" };
  return (
    <span className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-bold ${meta.color}`}>
      {meta.label}
    </span>
  );
}

function PlaybookCard({ pb, expanded, onToggle, matchCount }) {
  const c = colorClasses[pb.color];
  return (
    <div className={`rounded-xl border ${c.border} ${expanded ? c.bg : "bg-white"} transition`}>
      <button
        onClick={onToggle}
        className="w-full flex items-start gap-3 px-4 py-3 text-left"
      >
        <div className={`shrink-0 flex h-9 w-9 items-center justify-center rounded-lg ${c.icon}`}>
          <svg className="h-4.5 w-4.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d={pb.icon} />
          </svg>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="text-sm font-bold text-gray-900">{pb.situation}</p>
            {matchCount > 0 && (
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${c.tag}`}>
                {matchCount} live
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-gray-500 line-clamp-1">{pb.when}</p>
        </div>
        <svg
          className={`shrink-0 h-4 w-4 text-gray-400 transition-transform ${expanded ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {expanded && (
        <div className="px-4 pb-4 pt-1 grid gap-2.5">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-wide text-gray-500 mb-0.5">Why it happens</p>
            <p className="text-xs leading-relaxed text-gray-700">{pb.why}</p>
          </div>
          <div>
            <p className="text-[10px] font-bold uppercase tracking-wide text-gray-500 mb-0.5">The ACER play</p>
            <p className="text-xs leading-relaxed text-gray-700">{pb.play}</p>
          </div>
          <div>
            <p className="text-[10px] font-bold uppercase tracking-wide text-gray-500 mb-0.5">Signal to watch</p>
            <p className="text-xs leading-relaxed text-gray-700">{pb.signal}</p>
          </div>
        </div>
      )}
    </div>
  );
}

export default function SignalRadarPage() {
  const [items, setItems]         = useState([]);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState("");
  const [expandedPlaybook, setExpandedPlaybook] = useState("growing");
  const [infoOpen, setInfoOpen]   = useState(false);
  const [playbookOpen, setPlaybookOpen] = useState(true);

  const fetchSignals = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(apiUrl(`/api/news?days=7`));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const priority = (data.items || []).filter(it =>
        (it.categories || []).some(c => PRIORITY_CATEGORIES.includes(c))
      );
      const seen = new Set();
      const deduped = [];
      for (const it of priority) {
        const key = (it.company || it.subject || "").toLowerCase().slice(0, 60);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        deduped.push(it);
      }
      setItems(deduped);
    } catch (e) {
      setError(e.message || "Failed to fetch signals.");
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchSignals(); }, [fetchSignals]);

  const matchCounts = {};
  for (const pb of PLAYBOOKS) {
    matchCounts[pb.id] = items.filter(it =>
      (it.categories || []).some(c => pb.matchesCategories.includes(c))
    ).length;
  }

  const topSignals = items.slice(0, 25);

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-gray-50">

      {/* Header */}
      <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-bold text-gray-900">Signal Radar</h2>
            <button
              onClick={() => setInfoOpen(!infoOpen)}
              className="flex h-5 w-5 items-center justify-center rounded-full border border-gray-300 text-gray-400 hover:bg-gray-100 hover:text-gray-600 transition"
              title="What is this tab?"
            >
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </button>
          </div>
          <button
            onClick={fetchSignals}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 transition hover:bg-gray-50 disabled:opacity-50"
          >
            <svg className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Refresh
          </button>
        </div>

        {infoOpen && (
          <div className="mt-3 rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-xs leading-relaxed text-blue-800">
            <p className="font-semibold mb-1">What Signal Radar answers</p>
            <p>
              This is the "what do I do today?" tab. It curates the highest-priority signals from Market News
              (fund raises, rating actions, large expansions) and pairs them with a strategic playbook for each
              market phase. Every signal has a suggested next action. Deduplicated by company so you do not
              call the same lead twice.
            </p>
            <p className="mt-2 text-blue-600">
              Priority: Very High = regulatory action | High = capex above Rs 100 cr | Medium = sector activity
            </p>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-6 py-5 space-y-6">

          {/* Playbook section */}
          <section>
            <button
              onClick={() => setPlaybookOpen(!playbookOpen)}
              className="flex items-center gap-2 mb-2 group"
            >
              <svg
                className={`h-3.5 w-3.5 text-gray-400 group-hover:text-gray-600 transition-transform ${playbookOpen ? "rotate-90" : ""}`}
                fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
              </svg>
              <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 group-hover:text-gray-700">
                Playbook
              </h3>
              <span className="text-[10px] text-gray-400 font-normal normal-case">
                What to do in each market phase
              </span>
            </button>
            {playbookOpen && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {PLAYBOOKS.map(pb => (
                  <PlaybookCard
                    key={pb.id}
                    pb={pb}
                    expanded={expandedPlaybook === pb.id}
                    onToggle={() => setExpandedPlaybook(expandedPlaybook === pb.id ? null : pb.id)}
                    matchCount={matchCounts[pb.id]}
                  />
                ))}
              </div>
            )}
          </section>

          {/* Priority signals section */}
          <section>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500">
                Priority Signals This Week
              </h3>
              <span className="text-[10px] text-gray-400">
                {loading ? "Loading..." : `${items.length} companies, deduplicated`}
              </span>
            </div>

            {error && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 mb-3">
                <span className="font-semibold">Error: </span>{error}
              </div>
            )}

            {loading && (
              <div className="flex items-center justify-center py-12">
                <svg className="h-6 w-6 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              </div>
            )}

            {!loading && !error && topSignals.length === 0 && (
              <div className="rounded-xl border border-gray-200 bg-white px-6 py-10 text-center">
                <p className="text-sm font-semibold text-gray-900">No priority signals this week</p>
                <p className="mt-1 text-xs text-gray-500">
                  Check Market News for the full feed. Try widening to 14 or 30 days.
                </p>
              </div>
            )}

            {!loading && topSignals.length > 0 && (
              <div className="rounded-xl border border-gray-200 bg-white divide-y divide-gray-100 overflow-hidden">
                {topSignals.map((item, idx) => (
                  <div key={idx} className="flex items-start gap-4 px-4 py-3 hover:bg-gray-50 transition">
                    <div className="shrink-0 w-16 pt-0.5">
                      <span className="font-mono text-[11px] text-gray-400">{item.date}</span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        {item.company ? (
                          <span className="text-sm font-bold text-gray-900">{item.company}</span>
                        ) : (
                          <span className="text-sm font-semibold text-gray-800 line-clamp-1">
                            {item.subject?.slice(0, 90)}
                          </span>
                        )}
                        {item.symbol && (
                          <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-mono font-medium text-gray-500">
                            {item.symbol}
                          </span>
                        )}
                        {(item.categories || [])
                          .filter(c => PRIORITY_CATEGORIES.includes(c))
                          .map(cat => <CategoryTag key={cat} cat={cat} />)}
                        <span className="ml-1 text-[9px] text-gray-400">via {item.source}</span>
                      </div>
                      {item.company && item.subject && (
                        <p className="mt-0.5 text-[11px] leading-relaxed text-gray-600 line-clamp-1">
                          {item.subject}
                        </p>
                      )}
                    </div>
                    <div className="shrink-0 flex items-center gap-2 pt-0.5">
                      {(item.link || item.attachment) && (
                        <a
                          href={item.link || item.attachment}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[11px] font-medium text-blue-600 hover:underline whitespace-nowrap"
                        >
                          {item.source === "NSE" ? "Filing" : "Read"}
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Coming soon section */}
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 mb-2">
              Coming Soon
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {COMING_SOON.map((c, i) => (
                <div key={i} className="rounded-xl border border-dashed border-gray-300 bg-white px-4 py-3">
                  <div className="flex items-center gap-2 mb-1">
                    <p className="text-sm font-bold text-gray-700">{c.title}</p>
                    <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[9px] font-bold uppercase text-gray-500">
                      {c.eta}
                    </span>
                  </div>
                  <p className="text-xs leading-relaxed text-gray-500">{c.detail}</p>
                </div>
              ))}
            </div>
          </section>

        </div>
      </div>
    </div>
  );
}
