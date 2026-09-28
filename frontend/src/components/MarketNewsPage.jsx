import React, { useState, useEffect, useCallback } from "react";
import { apiUrl } from "../lib/api.js";
import { safeUrl } from "../lib/safeUrl.js";
import { EmptyState, FreshnessStrip } from "./SourceFreshness.jsx";

// Tab 3 - News, kept. Reads the archive (backend/pipeline/news_archive.py), not
// the live feed, so history is there and a dead feed shows as a dead feed.
// Every row carries its source link, the date ACER-IQ read it, and one line on
// why it matters to ACER (backend/pipeline/news_classify.py).

const KIND_META = {
  rating_action: { label: "Rating action",  color: "bg-amber-100 text-amber-800 border-amber-200" },
  debt_raise:    { label: "Debt raise",     color: "bg-blue-100 text-blue-700 border-blue-200" },
  credit_stress: { label: "Credit stress",  color: "bg-red-100 text-red-700 border-red-200" },
  capex:         { label: "Capex",          color: "bg-green-100 text-green-700 border-green-200" },
  deal:          { label: "Deal",           color: "bg-purple-100 text-purple-700 border-purple-200" },
  macro:         { label: "Macro",          color: "bg-slate-800 text-white border-slate-800" },
  equity_raise:  { label: "Equity raise",   color: "bg-gray-100 text-gray-600 border-gray-200" },
  results:       { label: "Results",        color: "bg-gray-100 text-gray-600 border-gray-200" },
  board_routine: { label: "Routine filing", color: "bg-gray-100 text-gray-500 border-gray-200" },
  market_chatter:{ label: "Market chatter", color: "bg-gray-100 text-gray-500 border-gray-200" },
  general:       { label: "General",        color: "bg-gray-100 text-gray-500 border-gray-200" },
};

const WINDOWS = [
  { value: 7, label: "7 days" },
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
  { value: 365, label: "1 year" },
];

function readDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso.slice(0, 10) : d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function NewsRow({ item }) {
  const kind = KIND_META[item.kind] ?? KIND_META.general;
  const title = item.company || item.subject;
  return (
    <div className="flex items-start gap-4 bg-white px-6 py-4 hover:bg-gray-50 transition">
      <div className="w-20 shrink-0 pt-0.5">
        <span className="font-mono text-xs text-gray-500">{item.published || item.date}</span>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-bold text-gray-900">{title}</span>
          {item.symbol && (
            <span className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-500">{item.symbol}</span>
          )}
          <span className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-bold ${kind.color}`}>{kind.label}</span>
          {(item.sector_labels ?? []).map((s) => (
            <span key={s} className="inline-flex rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[10px] text-gray-500">{s}</span>
          ))}
        </div>
        {item.company && item.subject && (
          <p className="mt-1 text-xs text-gray-600 line-clamp-2">{item.subject}</p>
        )}
        {item.description && item.description !== item.subject && (
          <p className="mt-0.5 text-xs text-gray-400 line-clamp-1">{item.description}</p>
        )}
        {/* Why it matters to ACER - never blank (rule-based, news_classify.py). */}
        <p className="mt-1.5 text-xs font-medium text-gray-800">
          <span className="text-blue-600">Why it matters: </span>{item.why}
        </p>
        {/* Source link + read date on every row (BUILD_PLAN invariant 2). */}
        <p className="mt-1 text-[11px] text-gray-400">
          Source:{" "}
          {safeUrl(item.link) ? (
            <a href={safeUrl(item.link)} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
              {item.source}
            </a>
          ) : (
            <span>{item.source} (no link published)</span>
          )}
          {" · "}read {readDate(item.read_at)}
        </p>
      </div>
    </div>
  );
}

export default function MarketNewsPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [days, setDays] = useState(7);
  const [majorOnly, setMajorOnly] = useState(true);
  const [kindFilter, setKindFilter] = useState("all");
  const [srcFilter, setSrcFilter] = useState("all");

  const fetchNews = useCallback(async (d, major, refresh = false) => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(apiUrl(`/api/news?days=${d}&major=${major}${refresh ? "&refresh=true" : ""}`));
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      setData(await res.json());
    } catch (e) {
      // "Couldn't reach the archive" must never render as "no news".
      setData(null);
      setError(`News archive unavailable: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchNews(days, majorOnly); }, [days, majorOnly, fetchNews]);

  const items = data?.items ?? [];
  const sources = ["all", ...new Set(items.map((i) => i.source))];
  const bySource = srcFilter === "all" ? items : items.filter((i) => i.source === srcFilter);
  const kindCounts = {};
  for (const it of bySource) kindCounts[it.kind] = (kindCounts[it.kind] ?? 0) + 1;
  const shown = kindFilter === "all" ? bySource : bySource.filter((i) => i.kind === kindFilter);
  const archive = data?.archive;

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-gray-50">
      <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-bold text-gray-900">Market News</h2>
            {archive && (
              <p className="mt-0.5 text-[11px] text-gray-400">
                Archive: {archive.items?.toLocaleString() ?? 0} items
                {archive.collecting_since && <> collected since {readDate(archive.collecting_since)}</>}
                {archive.durable === false && (
                  <span className="ml-1 font-semibold text-amber-700">
                    - not durable yet (server restart would wipe it)
                  </span>
                )}
              </p>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs text-gray-600">
              <input type="checkbox" checked={majorOnly}
                onChange={(e) => { setMajorOnly(e.target.checked); setKindFilter("all"); }} />
              Major only
            </label>
            <div className="flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
              {WINDOWS.map((o) => (
                <button key={o.value} onClick={() => setDays(o.value)}
                  className={`rounded-md px-3 py-1 text-xs font-medium transition ${
                    days === o.value ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-700"}`}>
                  {o.label}
                </button>
              ))}
            </div>
            <button onClick={() => fetchNews(days, majorOnly, true)} disabled={loading}
              className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 transition hover:bg-gray-50 disabled:opacity-50">
              {loading ? "Loading..." : "Refresh"}
            </button>
          </div>
        </div>
        {data?.freshness && <div className="mt-2"><FreshnessStrip rows={data.freshness} /></div>}
      </div>

      {items.length > 0 && (
        <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-2">
          <div className="flex flex-wrap items-center gap-1.5">
            {sources.map((s) => (
              <button key={s} onClick={() => { setSrcFilter(s); setKindFilter("all"); }}
                className={`rounded-lg px-3 py-1 text-xs font-medium transition ${
                  srcFilter === s ? "bg-gray-900 text-white" : "border border-gray-200 bg-gray-50 text-gray-500 hover:bg-gray-100"}`}>
                {s === "all" ? "All sources" : s}
              </button>
            ))}
            <span className="mx-1 text-gray-300">|</span>
            <button onClick={() => setKindFilter("all")}
              className={`rounded-full border px-3 py-0.5 text-xs ${kindFilter === "all" ? "border-gray-900 bg-gray-900 text-white" : "border-gray-200 text-gray-500"}`}>
              All ({bySource.length})
            </button>
            {Object.entries(KIND_META).map(([k, m]) => kindCounts[k] ? (
              <button key={k} onClick={() => setKindFilter(kindFilter === k ? "all" : k)}
                className={`rounded-full border px-3 py-0.5 text-xs ${kindFilter === k ? "border-gray-900 bg-gray-900 text-white" : "border-gray-200 text-gray-500"}`}>
                {m.label} ({kindCounts[k]})
              </button>
            ) : null)}
          </div>
        </div>
      )}

      {error && (
        <div className="shrink-0 border-b border-red-200 bg-red-50 px-6 py-2.5 text-sm text-red-700">
          <span className="font-semibold">Error: </span>{error}
        </div>
      )}

      {loading && !data && (
        <div className="flex flex-1 items-center justify-center text-sm text-gray-500">Reading the archive...</div>
      )}

      {!loading && !error && shown.length === 0 && (
        <div className="flex flex-1 items-center justify-center">
          {items.length > 0 ? (
            <p className="text-sm text-gray-500">No items match this filter.</p>
          ) : (
            <EmptyState
              verdict={data?.empty_means}
              quietTitle={majorOnly ? "No major news in this window" : "No news in this window"}
              quietText={majorOnly
                ? "Every feed answered; nothing with a credit consequence was published. Untick Major only to see everything read."
                : "Every feed answered and nothing was published in this window."}
            />
          )}
        </div>
      )}

      {shown.length > 0 && (
        <div className="flex-1 overflow-y-auto divide-y divide-gray-100">
          {shown.map((it, i) => <NewsRow key={`${it.source}-${it.link}-${i}`} item={it} />)}
          {data?.truncated && (
            <p className="bg-white px-6 py-3 text-xs text-gray-400">Showing the newest 500 - narrow the window or filter to see older items.</p>
          )}
        </div>
      )}
    </div>
  );
}
