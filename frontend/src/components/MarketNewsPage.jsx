import React, { useState, useEffect, useCallback } from "react";
import { apiUrl } from "../lib/api.js";

const CATEGORY_META = {
  fund_raise:     { label: "Fund Raise",     color: "bg-blue-100 text-blue-700 border-blue-200" },
  expansion:      { label: "Expansion",      color: "bg-green-100 text-green-700 border-green-200" },
  acquisition:    { label: "Acquisition",    color: "bg-purple-100 text-purple-700 border-purple-200" },
  rating_action:  { label: "Rating Action",  color: "bg-amber-100 text-amber-700 border-amber-200" },
  board_approval: { label: "Board Approval", color: "bg-gray-200 text-gray-700 border-gray-300" },
};

function CategoryTag({ cat }) {
  const meta = CATEGORY_META[cat] || { label: cat, color: "bg-gray-100 text-gray-600 border-gray-200" };
  return (
    <span className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-bold ${meta.color}`}>
      {meta.label}
    </span>
  );
}

export default function MarketNewsPage() {
  const [items, setItems]       = useState([]);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState("");
  const [meta, setMeta]         = useState(null);
  const [days, setDays]         = useState(7);
  const [filter, setFilter]     = useState("all");
  const [infoOpen, setInfoOpen] = useState(false);

  const fetchNews = useCallback(async (d) => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(apiUrl(`/api/news?days=${d}`));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.status === "blocked") {
        setError(data.message || "NSE is temporarily unreachable.");
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

  const filtered = filter === "all"
    ? items
    : items.filter(it => it.categories?.includes(filter));

  const categoryCounts = {};
  for (const it of items) {
    for (const c of it.categories || []) {
      categoryCounts[c] = (categoryCounts[c] || 0) + 1;
    }
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-gray-50">

      {/* Info bar */}
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
            {/* Date range */}
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
            {/* Refresh */}
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

        {/* Info panel (collapsible) */}
        {infoOpen && (
          <div className="mt-3 rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-xs leading-relaxed text-blue-800">
            <p className="font-semibold mb-1">How this tab works</p>
            <p>
              Tracks corporate announcements from NSE that signal upcoming funding needs.
              Every item here is a real exchange filing, not a prediction. Look for companies
              announcing expansions, capex approvals, fund raising resolutions, acquisitions,
              and rating actions. A company that announces a large expansion will need debt,
              and debt needs a rating. You call them before the Big 3 agencies do.
            </p>
            <p className="mt-2 text-blue-600">
              Source: NSE Corporate Announcements (live data, not cached)
            </p>
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="shrink-0 border-b border-red-200 bg-red-50 px-6 py-2.5 text-sm text-red-700">
          <span className="font-semibold">Error: </span>{error}
        </div>
      )}

      {/* Filter chips */}
      {items.length > 0 && (
        <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-2.5">
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={() => setFilter("all")}
              className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
                filter === "all"
                  ? "bg-gray-900 text-white border-gray-900"
                  : "bg-white text-gray-500 border-gray-200 hover:border-gray-300"
              }`}
            >
              All ({items.length})
            </button>
            {Object.entries(CATEGORY_META).map(([key, meta]) => {
              const count = categoryCounts[key] || 0;
              if (!count) return null;
              return (
                <button
                  key={key}
                  onClick={() => setFilter(filter === key ? "all" : key)}
                  className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
                    filter === key
                      ? "bg-gray-900 text-white border-gray-900"
                      : `bg-white text-gray-500 border-gray-200 hover:border-gray-300`
                  }`}
                >
                  {meta.label} ({count})
                </button>
              );
            })}
            {meta && (
              <span className="ml-auto text-[11px] text-gray-400">
                {meta.total_filtered || 0} signals from {meta.total_raw || 0} announcements
                {meta.from_date && ` | ${meta.from_date} to ${meta.to_date}`}
              </span>
            )}
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
            <p className="mt-3 text-sm text-gray-500">Fetching announcements from NSE...</p>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!loading && items.length === 0 && !error && (
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center px-6">
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gray-100">
              <svg className="h-7 w-7 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round"
                  d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z" />
              </svg>
            </div>
            <p className="text-base font-semibold text-gray-900">No signals found</p>
            <p className="mt-1.5 text-sm text-gray-500">
              No money-signal announcements in the last {days} days. Try a wider date range.
            </p>
          </div>
        </div>
      )}

      {/* News feed */}
      {!loading && filtered.length > 0 && (
        <div className="flex-1 overflow-y-auto">
          <div className="divide-y divide-gray-100">
            {filtered.map((item, idx) => (
              <div key={idx} className="flex items-start gap-4 bg-white px-6 py-4 hover:bg-gray-50 transition">
                {/* Date */}
                <div className="shrink-0 w-20 pt-0.5">
                  <span className="font-mono text-xs text-gray-400">{item.date}</span>
                </div>

                {/* Content */}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-bold text-gray-900">{item.company}</span>
                    {item.symbol && (
                      <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-mono font-medium text-gray-500">
                        {item.symbol}
                      </span>
                    )}
                    {(item.categories || []).map(cat => (
                      <CategoryTag key={cat} cat={cat} />
                    ))}
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-gray-600 line-clamp-2">
                    {item.subject}
                  </p>
                </div>

                {/* Actions */}
                <div className="shrink-0 flex items-center gap-2 pt-0.5">
                  {item.attachment && (
                    <a
                      href={item.attachment}
                      target="_blank"
                      rel="noreferrer"
                      className="text-[11px] font-medium text-blue-600 hover:underline whitespace-nowrap"
                    >
                      View filing
                    </a>
                  )}
                  <a
                    href={`https://www.nseindia.com/get-quotes/equity?symbol=${encodeURIComponent(item.symbol)}`}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[11px] font-medium text-gray-400 hover:text-blue-500 whitespace-nowrap"
                  >
                    NSE page
                  </a>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
