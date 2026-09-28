import React, { useState, useEffect, useCallback } from "react";
import { apiUrl } from "../lib/api.js";
import { safeUrl } from "../lib/safeUrl.js";
import { EmptyState, FreshnessStrip } from "./SourceFreshness.jsx";

// Tab 1 - Macro. A big thing happened: who does it hit, and do they now need a
// rating? backend/pipeline/macro.py joins major macro news -> sector ->
// companies with debt maturing inside 9 months or thin interest coverage ->
// ranked by winnability. Every name carries its reason and its sources.

function readDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? String(iso).slice(0, 10)
    : d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function WinBadge({ c }) {
  if (c.blocked) {
    return <span className="rounded-md border border-red-200 bg-red-50 px-2 py-1 text-xs font-bold text-red-600">Blocked</span>;
  }
  if (c.winnability == null) {
    return <span className="rounded-md border border-gray-200 bg-gray-50 px-2 py-1 text-xs text-gray-400" title="Not in the CRA archive yet">?</span>;
  }
  const color = c.winnability >= 70 ? "text-red-500 border-red-200 bg-red-50"
    : c.winnability >= 40 ? "text-orange-500 border-orange-200 bg-orange-50"
    : "text-gray-500 border-gray-200 bg-gray-50";
  return <span className={`rounded-md border px-2 py-1 text-xs font-bold ${color}`}>{c.winnability}</span>;
}

function SectorBlock({ block, inputsDown }) {
  return (
    <section className="rounded-xl border border-gray-200 bg-white">
      <header className="border-b border-gray-100 px-5 py-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-bold text-gray-900">{block.sector_label}</h3>
          <span className="text-[11px] text-gray-400">
            {block.company_count} named · {block.event_count} event{block.event_count === 1 ? "" : "s"}
          </span>
        </div>
        <ul className="mt-1.5 space-y-0.5">
          {block.events.map((e, i) => (
            <li key={i} className="text-xs text-gray-600">
              {safeUrl(e.link) ? (
                <a href={safeUrl(e.link)} target="_blank" rel="noreferrer" className="font-medium text-gray-800 hover:text-blue-600 hover:underline">
                  {e.subject}
                </a>
              ) : <span className="font-medium text-gray-800">{e.subject}</span>}
              <span className="text-gray-400"> · {e.source} · read {readDate(e.read_at)}</span>
            </li>
          ))}
        </ul>
      </header>
      {block.companies.length === 0 ? (
        inputsDown.length > 0 ? (
          <p className="px-5 py-3 text-xs font-medium text-red-700">
            No names because {inputsDown.join(" and ")} could not be read - this is missing data,
            not a sector with nobody exposed.
          </p>
        ) : (
          <p className="px-5 py-3 text-xs text-gray-500">
            Both inputs answered: no company here has debt maturing inside 9 months or thin
            interest coverage among those checked (see Coverage above).
          </p>
        )
      ) : (
        <ul className="divide-y divide-gray-100">
          {block.companies.map((c) => (
            <li key={c.company_name} className="flex items-start gap-3 px-5 py-3">
              <WinBadge c={c} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-gray-900">{c.company_name}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-gray-700">{c.reason}</p>
                <p className="mt-1 text-[11px] text-gray-400">
                  {c.triggers.map((t, i) => (
                    <span key={i}>
                      {i > 0 && " · "}
                      Source:{" "}
                      {safeUrl(t.url) ? (
                        <a href={safeUrl(t.url)} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">{t.source}</a>
                      ) : t.source}
                      {t.read_at && <> read {readDate(t.read_at)}</>}
                    </span>
                  ))}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function MacroPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [days, setDays] = useState(14);

  const load = useCallback(async (d) => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(apiUrl(`/api/macro?days=${d}`));
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      setData(await res.json());
    } catch (e) {
      setData(null);
      setError(`Macro list unavailable: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(days); }, [days, load]);

  const cov = data?.coverage;
  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-gray-50">
      <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-bold text-gray-900">Macro</h2>
            <p className="mt-0.5 text-[11px] text-gray-400">
              Major macro event → sectors it hits → companies there with debt maturing inside 9 months or thin interest coverage → ranked by winnability.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
              {[7, 14, 30].map((d) => (
                <button key={d} onClick={() => setDays(d)}
                  className={`rounded-md px-3 py-1 text-xs font-medium transition ${days === d ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-700"}`}>
                  {d} days
                </button>
              ))}
            </div>
            <button onClick={() => load(days)} disabled={loading}
              className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50">
              {loading ? "Building..." : "Refresh"}
            </button>
          </div>
        </div>
        {cov && (
          <div className={`mt-2 rounded-lg border px-3 py-1.5 text-[11px] ${cov.stale_inputs?.length ? "border-red-200 bg-red-50 text-red-700" : "border-amber-200 bg-amber-50 text-amber-700"}`}>
            <span className="font-semibold">Coverage: </span>
            {cov.stale_inputs?.length > 0 && <>Could not read {cov.stale_inputs.join(", ")} - names that input would add are missing. </>}
            {cov.note} Winnability is known for {cov.winnability_known_for ?? 0} issuers in the CRA archive; the rest show "?".
          </div>
        )}
        {data?.freshness && <div className="mt-2"><FreshnessStrip rows={data.freshness} /></div>}
      </div>

      {error && (
        <div className="shrink-0 border-b border-red-200 bg-red-50 px-6 py-2.5 text-sm text-red-700">
          <span className="font-semibold">Error: </span>{error}
        </div>
      )}

      {loading && !data && (
        <div className="flex flex-1 items-center justify-center text-sm text-gray-500">
          Joining macro news with maturities and filed financials - the first build can take a minute...
        </div>
      )}

      {!loading && !error && data && data.sectors.length === 0 && (
        <div className="flex flex-1 items-center justify-center">
          <EmptyState
            verdict={data.empty_means}
            quietTitle="No major macro event in this window"
            quietText="Every news feed answered; nothing economy- or sector-wide with a credit consequence was published."
          />
        </div>
      )}

      {data && data.sectors.length > 0 && (
        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
          {data.sectors.map((b) => (
            <SectorBlock key={b.sector} block={b} inputsDown={cov?.stale_inputs ?? []} />
          ))}
          <p className="text-[11px] text-gray-400">Built {readDate(data.built_at)} · cached 30 minutes.</p>
        </div>
      )}
    </div>
  );
}
