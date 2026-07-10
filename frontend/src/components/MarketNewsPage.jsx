import React, { useState, useEffect, useCallback } from "react";
import { apiUrl } from "../lib/api.js";

const CATEGORY_META = {
  fund_raise:     { label: "Fund Raise",     color: "bg-blue-100 text-blue-700 border-blue-200" },
  expansion:      { label: "Expansion",      color: "bg-green-100 text-green-700 border-green-200" },
  acquisition:    { label: "Acquisition",    color: "bg-purple-100 text-purple-700 border-purple-200" },
  rating_action:  { label: "Rating Action",  color: "bg-amber-100 text-amber-700 border-amber-200" },
  board_approval: { label: "Board Approval", color: "bg-gray-200 text-gray-700 border-gray-300" },
  market_move:    { label: "Market Move",    color: "bg-rose-100 text-rose-700 border-rose-200" },
  results:        { label: "Results",        color: "bg-teal-100 text-teal-700 border-teal-200" },
};

const SOURCE_META = {
  all:              { label: "All Sources",       icon: "M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15" },
  NSE:              { label: "NSE Filings",       icon: "M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z" },
  "Economic Times": { label: "Economic Times",   icon: "M12 7.5h1.5m-1.5 3h1.5m-7.5 3h7.5m-7.5 3h7.5m3-9h3.375c.621 0 1.125.504 1.125 1.125V18a2.25 2.25 0 01-2.25 2.25M16.5 7.5V18a2.25 2.25 0 002.25 2.25M16.5 7.5V4.875c0-.621-.504-1.125-1.125-1.125H4.125C3.504 3.75 3 4.254 3 4.875V18a2.25 2.25 0 002.25 2.25h13.5" },
  LiveMint:         { label: "LiveMint",          icon: "M12 7.5h1.5m-1.5 3h1.5m-7.5 3h7.5m-7.5 3h7.5m3-9h3.375c.621 0 1.125.504 1.125 1.125V18a2.25 2.25 0 01-2.25 2.25M16.5 7.5V18a2.25 2.25 0 002.25 2.25M16.5 7.5V4.875c0-.621-.504-1.125-1.125-1.125H4.125C3.504 3.75 3 4.254 3 4.875V18a2.25 2.25 0 002.25 2.25h13.5" },
};

function CategoryTag({ cat }) {
  const meta = CATEGORY_META[cat] || { label: cat, color: "bg-gray-100 text-gray-600 border-gray-200" };
  return (
    <span className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-bold ${meta.color}`}>
      {meta.label}
    </span>
  );
}

function SourceBadge({ source }) {
  const colors = {
    NSE: "bg-blue-50 text-blue-700 border-blue-200",
    "Economic Times": "bg-orange-50 text-orange-700 border-orange-200",
    LiveMint: "bg-emerald-50 text-emerald-700 border-emerald-200",
  };
  return (
    <span className={`inline-flex rounded border px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide ${colors[source] || "bg-gray-50 text-gray-500 border-gray-200"}`}>
      {source}
    </span>
  );
}

function getItemLink(item) {
  if (item.link) return item.link;
  if (item.attachment) return item.attachment;
  if (item.symbol) return `https://www.nseindia.com/get-quotes/equity?symbol=${encodeURIComponent(item.symbol)}`;
  return "";
}

function getSourcePageLink(item) {
  if (item.source === "NSE" && item.symbol) {
    return `https://www.nseindia.com/get-quotes/equity?symbol=${encodeURIComponent(item.symbol)}`;
  }
  if (item.source === "Economic Times") return "https://economictimes.indiatimes.com/markets";
  if (item.source === "LiveMint") return "https://www.livemint.com/market";
  return "";
}

export default function MarketNewsPage() {
  const [items, setItems]           = useState([]);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState("");
  const [meta, setMeta]             = useState(null);
  const [days, setDays]             = useState(7);
  const [catFilter, setCatFilter]   = useState("all");
  const [srcFilter, setSrcFilter]   = useState("all");
  const [infoOpen, setInfoOpen]     = useState(false);

  const fetchNews = useCallback(async (d) => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(apiUrl(`/api/news?days=${d}`));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.status === "blocked") {
        setError(data.message || "News sources are temporarily unreachable.");
        setItems([]);
      } else {
        setItems(data.items || []);
      }
      setMeta(data);
    } catch (e) {
      setError(e.message || "Failed to fetch news. Make sure the backend is running.");
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchNews(days); }, [days, fetchNews]);

  const availableSources = ["all"];
  const sourceCounts = {};
  for (const it of items) {
    const s = it.source || "Unknown";
    sourceCounts[s] = (sourceCounts[s] || 0) + 1;
    if (!availableSources.includes(s)) availableSources.push(s);
  }

  let filtered = srcFilter === "all"
    ? items
    : items.filter(it => it.source === srcFilter);

  if (catFilter !== "all") {
    filtered = filtered.filter(it => it.categories?.includes(catFilter));
  }

  const categoryCounts = {};
  const baseForCats = srcFilter === "all" ? items : items.filter(it => it.source === srcFilter);
  for (const it of baseForCats) {
    for (const c of it.categories || []) {
      categoryCounts[c] = (categoryCounts[c] || 0) + 1;
    }
  }

  const signalCount = baseForCats.filter(it => it.categories?.length > 0).length;
  const generalCount = baseForCats.filter(it => !it.categories?.length).length;

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-gray-50">

      {/* Header bar */}
      <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-bold text-gray-900">Market News</h2>
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
          <div className="flex items-center gap-3">
            <div className="flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
              {[
                { value: 7,  label: "7 days" },
                { value: 14, label: "14 days" },
                { value: 30, label: "30 days" },
              ].map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setDays(opt.value)}
                  className={`rounded-md px-3 py-1 text-xs font-medium transition ${
                    days === opt.value
                      ? "bg-white text-gray-900 shadow-sm"
                      : "text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <button
              onClick={() => fetchNews(days)}
              disabled={loading}
              className="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 transition hover:bg-gray-50 disabled:opacity-50"
            >
              <svg className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              Refresh
            </button>
          </div>
        </div>

        {infoOpen && (
          <div className="mt-3 rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-xs leading-relaxed text-blue-800">
            <p className="font-semibold mb-1">How this tab works</p>
            <p>
              Aggregates market signals from multiple sources: NSE corporate announcements
              (real exchange filings), Economic Times, and LiveMint. Each item is tagged with
              signal categories like fund raising, expansion, acquisition, and rating actions.
              Items with no signal tags are general market news. You can filter by source
              and by signal category. Every item links back to the original source.
            </p>
            <p className="mt-2 text-blue-600">
              Sources: NSE Corporate Announcements + Economic Times RSS + LiveMint RSS (live data)
            </p>
          </div>
        )}
      </div>

      {/* Source tabs */}
      <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-2">
        <div className="flex items-center gap-1.5">
          {availableSources.map(src => {
            const sm = SOURCE_META[src] || { label: src, icon: "" };
            const count = src === "all" ? items.length : (sourceCounts[src] || 0);
            const active = srcFilter === src;
            return (
              <button
                key={src}
                onClick={() => { setSrcFilter(src); setCatFilter("all"); }}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                  active
                    ? "bg-gray-900 text-white"
                    : "bg-gray-50 text-gray-500 hover:bg-gray-100 hover:text-gray-700 border border-gray-200"
                }`}
              >
                {sm.icon && (
                  <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d={sm.icon} />
                  </svg>
                )}
                {sm.label}
                <span className={`ml-0.5 text-[10px] ${active ? "text-gray-300" : "text-gray-400"}`}>
                  ({count})
                </span>
              </button>
            );
          })}
          {meta && (
            <span className="ml-auto text-[10px] text-gray-400">
              {meta.sources?.filter(s => !s.includes("failed") && !s.includes("unreachable")).join(" + ") || ""}
            </span>
          )}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="shrink-0 border-b border-red-200 bg-red-50 px-6 py-2.5 text-sm text-red-700">
          <span className="font-semibold">Error: </span>{error}
        </div>
      )}

      {/* Category filter chips */}
      {baseForCats.length > 0 && (
        <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-2.5">
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={() => setCatFilter("all")}
              className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
                catFilter === "all"
                  ? "bg-gray-900 text-white border-gray-900"
                  : "bg-white text-gray-500 border-gray-200 hover:border-gray-300"
              }`}
            >
              All ({baseForCats.length})
            </button>
            {signalCount > 0 && (
              <span className="text-[10px] text-gray-300 mx-1">|</span>
            )}
            {Object.entries(CATEGORY_META).map(([key, meta]) => {
              const count = categoryCounts[key] || 0;
              if (!count) return null;
              return (
                <button
                  key={key}
                  onClick={() => setCatFilter(catFilter === key ? "all" : key)}
                  className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
                    catFilter === key
                      ? "bg-gray-900 text-white border-gray-900"
                      : `bg-white text-gray-500 border-gray-200 hover:border-gray-300`
                  }`}
                >
                  {meta.label} ({count})
                </button>
              );
            })}
            <span className="ml-auto text-[11px] text-gray-400">
              {signalCount} signals, {generalCount} general
              {meta?.from_date && ` | ${meta.from_date} to ${meta.to_date}`}
            </span>
          </div>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center">
            <svg className="mx-auto h-8 w-8 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <p className="mt-3 text-sm text-gray-500">Fetching from NSE + Economic Times + LiveMint...</p>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!loading && filtered.length === 0 && !error && (
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center px-6">
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gray-100">
              <svg className="h-7 w-7 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round"
                  d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z" />
              </svg>
            </div>
            <p className="text-base font-semibold text-gray-900">No news found</p>
            <p className="mt-1.5 text-sm text-gray-500">
              No items match this filter. Try "All Sources" or a wider date range.
            </p>
          </div>
        </div>
      )}

      {/* News feed */}
      {!loading && filtered.length > 0 && (
        <div className="flex-1 overflow-y-auto">
          <div className="divide-y divide-gray-100">
            {filtered.map((item, idx) => {
              const primaryLink = getItemLink(item);
              const sourceLink = getSourcePageLink(item);
              return (
                <div key={idx} className="flex items-start gap-4 bg-white px-6 py-4 hover:bg-gray-50 transition">
                  {/* Date */}
                  <div className="shrink-0 w-20 pt-0.5">
                    <span className="font-mono text-xs text-gray-400">{item.date}</span>
                  </div>

                  {/* Content */}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      {item.company ? (
                        <span className="text-sm font-bold text-gray-900">{item.company}</span>
                      ) : (
                        <span className="text-sm font-semibold text-gray-800 line-clamp-1">
                          {item.subject?.slice(0, 80)}
                        </span>
                      )}
                      {item.symbol && (
                        <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-mono font-medium text-gray-500">
                          {item.symbol}
                        </span>
                      )}
                      <SourceBadge source={item.source} />
                      {(item.categories || []).map(cat => (
                        <CategoryTag key={cat} cat={cat} />
                      ))}
                    </div>
                    {item.company && (
                      <p className="mt-1 text-xs leading-relaxed text-gray-600 line-clamp-2">
                        {item.subject}
                      </p>
                    )}
                    {!item.company && item.description && (
                      <p className="mt-1 text-xs leading-relaxed text-gray-500 line-clamp-2">
                        {item.description}
                      </p>
                    )}
                  </div>

                  {/* Actions */}
                  <div className="shrink-0 flex items-center gap-2 pt-0.5">
                    {primaryLink && (
                      <a
                        href={primaryLink}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[11px] font-medium text-blue-600 hover:underline whitespace-nowrap"
                      >
                        {item.source === "NSE" ? "View filing" : "Read more"}
                      </a>
                    )}
                    {sourceLink && sourceLink !== primaryLink && (
                      <a
                        href={sourceLink}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[11px] font-medium text-gray-400 hover:text-blue-500 whitespace-nowrap"
                      >
                        {item.source === "NSE" ? "NSE page" : item.source}
                      </a>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
