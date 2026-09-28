import React, { useState, useEffect, useCallback } from "react";
import { apiUrl } from "../lib/api.js";
import { safeUrl } from "../lib/safeUrl.js";

const CATEGORY_META = {
  fund_raise:    { label: "Fund Raise",    color: "bg-blue-100 text-blue-700 border-blue-200" },
  rating_action: { label: "Rating Action", color: "bg-amber-100 text-amber-700 border-amber-200" },
  expansion:     { label: "Expansion",     color: "bg-green-100 text-green-700 border-green-200" },
  acquisition:   { label: "Acquisition",   color: "bg-purple-100 text-purple-700 border-purple-200" },
  board_approval:{ label: "Board Approval",color: "bg-gray-200 text-gray-700 border-gray-300" },
};

const BULLISH_CATEGORIES = ["fund_raise", "expansion", "board_approval"];
const BEARISH_CATEGORIES = ["rating_action"];

const PLAYBOOKS = {
  bullish: [
    {
      id: "growing",
      icon: "M13 7h8m0 0v8m0-8l-8 8-4-4-6 6",
      color: "emerald",
      situation: "Sector on the rise",
      when: "When a sector is expanding: renewables, defense, EMS, real estate cycles up.",
      why: "Companies raise fresh debt for capex. NCDs, bonds, and bank loans surge. Debut issuers appear.",
      play: "Chase debut issuers before Big 3 lock them in. Offer speed and price on first mandates. Win at debut, keep through surveillance.",
      signal: "Filter Market News by Fund Raise + Expansion. Look for first-time NCD board approvals.",
      sourceLink: "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
      sourceLabel: "NSE Corporate Announcements",
    },
    {
      id: "first_time",
      icon: "M12 4v16m8-8H4",
      color: "blue",
      situation: "First-time issuers",
      when: "When a company issues debt for the first time: newer NBFCs, IPO-stage companies, mid-tier corporates.",
      why: "No incumbent CRA relationship to displace. Zero switching cost. Highest conversion rate.",
      play: "Target companies filing board approvals for their first NCD or first commercial paper. Bank BLR mandates for first-time borrowers are equally valuable.",
      signal: "Board Approval + Fund Raise signals. Company Research shows whether they have been rated before.",
      sourceLink: "https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRecognisedCra=yes",
      sourceLabel: "SEBI CRA list (verify existing ratings)",
    },
  ],
  bearish: [
    {
      id: "declining",
      icon: "M13 17h8m0 0V9m0 8l-8-8-4 4-6-6",
      color: "rose",
      situation: "Sector under stress",
      when: "When a sector faces headwinds: MFI collapse, real estate stress, NBFC liquidity crunch.",
      why: "Existing CRAs downgrade or withdraw ratings. Companies get pushed off preferred lists. Rating shopping intensifies.",
      play: "Approach companies within 30 days of a rating withdrawal or downgrade by CRISIL/ICRA/CARE. They are forced to re-rate within 90 days by regulation.",
      signal: "Filter Market News by Rating Action. Cross-check with the CRA's own website for the type of action.",
      sourceLink: "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=6&smid=0",
      sourceLabel: "SEBI CRA regulations (re-rating rules)",
    },
    {
      id: "stable",
      icon: "M5 12h14",
      color: "gray",
      situation: "Surveillance renewals",
      when: "In stable sectors: FMCG, mature manufacturing, utilities.",
      why: "Little new debt, but annual surveillance renewals dominate. Companies are fee-sensitive on renewal.",
      play: "Target incumbents 30 days before their annual surveillance expiry. Offer a 15 to 20 percent discount on surveillance to displace the incumbent CRA.",
      signal: "Requires the surveillance calendar (V2 feature). For now, use Company Research to check renewal timing.",
      sourceLink: "",
      sourceLabel: "",
    },
  ],
};

const COMING_SOON = [
  {
    title: "Rating Withdrawal Monitor",
    detail: "Weekly scrape of all 7 CRA websites for withdrawn and downgraded ratings. RBI mandates re-rating within 90 days.",
    eta: "Weeks 3 to 4",
    sourceLink: "https://www.crisilratings.com/en/home/rating-actions.html",
    sourceLabel: "Sample: CRISIL rating actions",
  },
  {
    title: "Surveillance Calendar",
    detail: "Extract next surveillance date from CRA rating rationale PDFs. Shows which companies come up for renewal each week.",
    eta: "Weeks 5 to 6",
    sourceLink: "https://www.icra.in/RatingRationale",
    sourceLabel: "Sample: ICRA rating rationales",
  },
  {
    title: "BLR Expiration Tracker",
    detail: "Bank loan ratings expire annually. Track expiries across the RBI accredited borrower base.",
    eta: "Weeks 7 to 8",
    sourceLink: "https://www.rbi.org.in/Scripts/BS_ViewMasCirculardetails.aspx?id=12362",
    sourceLabel: "RBI master circular on BLR",
  },
  {
    title: "SEBI EBP Bidding Signals",
    detail: "Track upcoming bond auctions on the Electronic Book Provider platform.",
    eta: "Weeks 9 to 10",
    sourceLink: "https://ebp.nseindia.com/",
    sourceLabel: "NSE EBP platform",
  },
];

const colorClasses = {
  emerald: { border: "border-emerald-200", bg: "bg-emerald-50", icon: "bg-emerald-100 text-emerald-700", tag: "bg-emerald-100 text-emerald-800" },
  rose:    { border: "border-rose-200",    bg: "bg-rose-50",    icon: "bg-rose-100 text-rose-700",       tag: "bg-rose-100 text-rose-800" },
  gray:    { border: "border-gray-200",    bg: "bg-gray-50",    icon: "bg-gray-100 text-gray-700",       tag: "bg-gray-100 text-gray-800" },
  blue:    { border: "border-blue-200",    bg: "bg-blue-50",    icon: "bg-blue-100 text-blue-700",       tag: "bg-blue-100 text-blue-800" },
};

function CategoryTag({ cat }) {
  const meta = CATEGORY_META[cat] || { label: cat, color: "bg-gray-100 text-gray-600 border-gray-200" };
  return (
    <span className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-bold ${meta.color}`}>
      {meta.label}
    </span>
  );
}

function PlaybookCard({ pb, matchCount }) {
  const [expanded, setExpanded] = useState(false);
  const c = colorClasses[pb.color];
  return (
    <div className={`rounded-xl border ${c.border} ${expanded ? c.bg : "bg-white"} transition`}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-start gap-3 px-4 py-3 text-left"
      >
        <div className={`shrink-0 flex h-9 w-9 items-center justify-center rounded-lg ${c.icon}`}>
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
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
          {pb.sourceLink && (
            <div className="pt-1 border-t border-gray-200">
              <a
                href={pb.sourceLink}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-[10px] font-semibold text-blue-600 hover:underline"
              >
                <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                </svg>
                Source: {pb.sourceLabel}
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SectorPill({ sector, mode }) {
  const isUp = sector.pct_change > 0;
  const color = mode === "bullish"
    ? "bg-emerald-50 text-emerald-800 border-emerald-200"
    : "bg-rose-50 text-rose-800 border-rose-200";
  return (
    <a
      href={sector.link}
      target="_blank"
      rel="noreferrer"
      className={`inline-flex items-center gap-1.5 rounded-lg border ${color} px-2.5 py-1.5 text-xs font-medium hover:shadow-sm transition group`}
    >
      <span className="font-bold">{sector.short_name}</span>
      <span className={`font-mono font-bold ${isUp ? "text-emerald-700" : "text-rose-700"}`}>
        {isUp ? "+" : ""}{sector.pct_change}%
      </span>
      <svg className="h-2.5 w-2.5 opacity-40 group-hover:opacity-100" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
      </svg>
    </a>
  );
}

export default function SignalRadarPage() {
  const [mode, setMode] = useState("bullish");
  const [items, setItems] = useState([]);
  const [sectors, setSectors] = useState({ bullish: [], bearish: [], status: "loading" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [infoOpen, setInfoOpen] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [newsRes, sectorsRes] = await Promise.all([
        fetch(apiUrl(`/api/news?days=7&major=false`)),
        fetch(apiUrl(`/api/sectors`)),
      ]);
      if (!newsRes.ok) throw new Error(`News: HTTP ${newsRes.status}`);
      const newsData = await newsRes.json();
      const sectorsData = sectorsRes.ok ? await sectorsRes.json() : { bullish: [], bearish: [], status: "blocked" };

      const seen = new Set();
      const deduped = [];
      for (const it of (newsData.items || [])) {
        const key = (it.company || it.subject || "").toLowerCase().slice(0, 60);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        deduped.push(it);
      }
      setItems(deduped);
      setSectors(sectorsData);
    } catch (e) {
      setError(e.message || "Failed to fetch signals.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const activeCategories = mode === "bullish" ? BULLISH_CATEGORIES : BEARISH_CATEGORIES;
  const activePlaybooks = PLAYBOOKS[mode];
  const activeSectors = mode === "bullish" ? sectors.bullish : sectors.bearish;

  const filteredItems = items.filter(it =>
    (it.categories || []).some(c => activeCategories.includes(c))
  );

  const playbookMatchCounts = {};
  for (const pb of [...PLAYBOOKS.bullish, ...PLAYBOOKS.bearish]) {
    playbookMatchCounts[pb.id] = items.filter(it => {
      const cats = it.categories || [];
      if (pb.id === "growing") return cats.some(c => ["fund_raise", "expansion"].includes(c));
      if (pb.id === "first_time") return cats.some(c => ["fund_raise", "board_approval"].includes(c));
      if (pb.id === "declining") return cats.includes("rating_action");
      return 0;
    }).length;
  }

  const topSignals = filteredItems.slice(0, 30);

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
            >
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </button>
          </div>
          <button
            onClick={fetchAll}
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
            <p className="font-semibold mb-1">What Signal Radar does</p>
            <p>
              Monitors NCD/bond board approvals, rating withdrawals, surveillance renewals, and bank loan
              rating expirations from BSE/NSE filings and CRA press releases. Companies with active signals
              need a rating within weeks. Splits the view by market direction: Bullish shows sectors rising
              today and companies raising debt in those sectors. Bearish shows falling sectors and companies
              with rating actions where an incumbent CRA can be displaced.
            </p>
            <p className="mt-2">
              <a href="https://www.nseindia.com/market-data/live-market-indices" target="_blank" rel="noreferrer"
                 className="text-blue-600 hover:underline font-semibold">Sector data source: NSE Live Market Indices</a>
              {" | "}
              <a href="https://www.nseindia.com/companies-listing/corporate-filings-announcements" target="_blank" rel="noreferrer"
                 className="text-blue-600 hover:underline font-semibold">Signal source: NSE Corporate Announcements</a>
            </p>
          </div>
        )}
      </div>

      {/* Mode tabs */}
      <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-2.5">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setMode("bullish")}
            className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-bold transition ${
              mode === "bullish"
                ? "bg-emerald-600 text-white shadow-sm"
                : "bg-white text-gray-500 border border-gray-200 hover:border-emerald-300 hover:text-emerald-700"
            }`}
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
            </svg>
            Bullish
            <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${mode === "bullish" ? "bg-white/25 text-white" : "bg-gray-100 text-gray-600"}`}>
              {sectors.bullish?.length || 0} sectors up
            </span>
          </button>
          <button
            onClick={() => setMode("bearish")}
            className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-bold transition ${
              mode === "bearish"
                ? "bg-rose-600 text-white shadow-sm"
                : "bg-white text-gray-500 border border-gray-200 hover:border-rose-300 hover:text-rose-700"
            }`}
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 17h8m0 0V9m0 8l-8-8-4 4-6-6" />
            </svg>
            Bearish
            <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${mode === "bearish" ? "bg-white/25 text-white" : "bg-gray-100 text-gray-600"}`}>
              {sectors.bearish?.length || 0} sectors down
            </span>
          </button>
          <span className="ml-auto text-[10px] text-gray-400">
            Sector data:{" "}
            <a href="https://www.nseindia.com/market-data/live-market-indices" target="_blank" rel="noreferrer" className="hover:underline text-gray-500">
              NSE Live Indices ↗
            </a>
          </span>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-6 py-5 space-y-6">

          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              <span className="font-semibold">Error: </span>{error}
            </div>
          )}

          {/* Sector heatmap */}
          <section>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500">
                {mode === "bullish" ? "Top rising sectors today" : "Top falling sectors today"}
              </h3>
              {sectors.status === "ok" && (
                <a
                  href="https://www.nseindia.com/market-data/live-market-indices"
                  target="_blank"
                  rel="noreferrer"
                  className="text-[10px] font-semibold text-blue-600 hover:underline"
                >
                  View all on NSE ↗
                </a>
              )}
            </div>
            {sectors.status === "loading" || loading ? (
              <div className="rounded-xl border border-gray-200 bg-white p-4">
                <div className="text-xs text-gray-400">Loading sector data...</div>
              </div>
            ) : sectors.status === "blocked" ? (
              <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                <div className="text-xs text-amber-700">NSE sector data unreachable. {sectors.message || ""}</div>
              </div>
            ) : activeSectors.length === 0 ? (
              <div className="rounded-xl border border-gray-200 bg-white p-4">
                <div className="text-xs text-gray-400">
                  No {mode === "bullish" ? "rising" : "falling"} sectors right now.
                </div>
              </div>
            ) : (
              <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
                <div className="flex flex-wrap gap-2">
                  {activeSectors.map(s => <SectorPill key={s.name} sector={s} mode={mode} />)}
                </div>
                <p className="mt-3 text-[10px] text-gray-400">
                  Live percent change vs previous close. Click any sector to see constituents on NSE.
                </p>
              </div>
            )}
          </section>

          {/* Playbook */}
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 mb-2">
              {mode === "bullish" ? "Bullish playbook" : "Bearish playbook"}
              <span className="ml-2 text-[10px] text-gray-400 font-normal normal-case">
                (Click any card to expand)
              </span>
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {activePlaybooks.map(pb => (
                <PlaybookCard
                  key={pb.id}
                  pb={pb}
                  matchCount={playbookMatchCounts[pb.id] || 0}
                />
              ))}
            </div>
          </section>

          {/* Priority signals */}
          <section>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500">
                {mode === "bullish" ? "Companies raising debt or expanding" : "Companies with rating actions (displacement targets)"}
              </h3>
              <span className="text-[10px] text-gray-400">
                {loading ? "Loading..." : `${filteredItems.length} deduplicated`}
              </span>
            </div>

            {loading ? (
              <div className="flex items-center justify-center py-10">
                <svg className="h-6 w-6 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              </div>
            ) : topSignals.length === 0 ? (
              <div className="rounded-xl border border-gray-200 bg-white px-6 py-8 text-center">
                <p className="text-sm font-semibold text-gray-900">No signals in this direction this week</p>
                <p className="mt-1 text-xs text-gray-500">Try the other tab or check Market News for the full feed.</p>
              </div>
            ) : (
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
                          .filter(c => activeCategories.includes(c))
                          .map(cat => <CategoryTag key={cat} cat={cat} />)}
                        <span className="ml-1 text-[9px] text-gray-400">via {item.source}</span>
                      </div>
                      {item.company && item.subject && (
                        <p className="mt-0.5 text-[11px] leading-relaxed text-gray-600 line-clamp-1">
                          {item.subject}
                        </p>
                      )}
                    </div>
                    <div className="shrink-0 flex items-center gap-3 pt-0.5">
                      {safeUrl(item.link || item.attachment) && (
                        <a
                          href={safeUrl(item.link || item.attachment)}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-0.5 text-[11px] font-medium text-blue-600 hover:underline whitespace-nowrap"
                        >
                          {item.source === "NSE" ? "Filing" : "Read"} ↗
                        </a>
                      )}
                      {item.symbol && (
                        <a
                          href={`https://www.nseindia.com/get-quotes/equity?symbol=${encodeURIComponent(item.symbol)}`}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[11px] font-medium text-gray-400 hover:text-blue-500 whitespace-nowrap"
                        >
                          NSE ↗
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Coming soon */}
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
                  {c.sourceLink && (
                    <a
                      href={c.sourceLink}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 mt-2 text-[10px] font-semibold text-blue-600 hover:underline"
                    >
                      <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                      </svg>
                      {c.sourceLabel}
                    </a>
                  )}
                </div>
              ))}
            </div>
          </section>

        </div>
      </div>
    </div>
  );
}
