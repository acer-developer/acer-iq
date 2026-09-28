import React, { useState, useEffect, useCallback } from "react";
import { apiFetch } from "../lib/auth.js";
import { safeUrl } from "../lib/safeUrl.js";
import { useProfile } from "../lib/profile.js";

// Tab 2 - the monthly BD list (backend/pipeline/bd_list.py). Generated once a
// month and frozen; Admin sees all four BDs, a BD profile sees only theirs.

function readDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? String(iso).slice(0, 10)
    : d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

const INPUT_LABEL = { queue: "Rating-action queue", macro: "Macro", news: "Company news", refinance: "Debt maturities (BSE)" };

async function addToPipeline(row, owner) {
  const res = await apiFetch("/api/leads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      company_name: row.company_name, winnability: row.winnability,
      flags: { bd_list: true, signals: row.signals, instrument: row.instrument },
      agencies_seen: [], owner,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(res.status === 401 ? "sign in to save leads" : body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

function Row({ row, owner }) {
  const [state, setState] = useState("");
  const add = async () => {
    setState("saving");
    try {
      const r = await addToPipeline(row, owner);
      setState(r.already_saved ? "already in a pipeline" : "added");
    } catch (e) { setState(`failed: ${e.message}`); }
  };
  return (
    <li className="flex items-start gap-3 px-5 py-3">
      <span className="w-6 shrink-0 pt-0.5 text-right font-mono text-xs text-gray-400">{row.rank}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-gray-900">{row.company_name}</span>
          <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[10px] font-bold text-blue-700">
            {row.instrument}
          </span>
          <span className="text-[10px] text-gray-400">
            winnability {row.winnability ?? "?"} · score {row.score}
          </span>
        </div>
        <p className="mt-0.5 text-xs text-gray-700">{row.reason}</p>
        <p className="mt-0.5 text-xs font-medium text-gray-800"><span className="text-blue-600">Play: </span>{row.play}</p>
        <p className="mt-1 text-[11px] text-gray-400">
          {row.sources.map((s, i) => (
            <span key={i}>
              {i > 0 && " · "}
              {safeUrl(s.url) ? <a href={safeUrl(s.url)} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">{s.label}</a> : s.label}
              {s.read_at && <> read {readDate(s.read_at)}</>}
            </span>
          ))}
        </p>
      </div>
      <div className="shrink-0 text-right">
        <button onClick={add} disabled={state === "saving" || state === "added"}
          className="rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50">
          {state === "added" ? "In pipeline" : state === "saving" ? "Saving..." : "Add to pipeline"}
        </button>
        {state && state !== "saving" && state !== "added" && (
          <p className={`mt-1 max-w-[12rem] text-[10px] ${state.startsWith("failed") ? "text-red-600" : "text-gray-500"}`}>{state}</p>
        )}
      </div>
    </li>
  );
}

export default function BDListPage() {
  const { profile, bds, isAdmin } = useProfile();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (regenerate = false) => {
    setLoading(true);
    setError("");
    try {
      const res = await apiFetch(regenerate ? "/api/bd-list/regenerate" : "/api/bd-list",
                                 regenerate ? { method: "POST" } : {});
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      setData(await res.json());
    } catch (e) {
      setData(null);
      setError(`BD list unavailable: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const rows = data?.rows ?? [];
  const shownBds = isAdmin ? bds : bds.filter((b) => b.id === profile.id);
  const bad = Object.entries(data?.inputs ?? {}).filter(([, v]) => v.status !== "ok");

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-gray-50">
      <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-bold text-gray-900">
              BD List {data?.month && <span className="font-normal text-gray-500">- {data.month}</span>}
              {!isAdmin && <span className="ml-2 font-normal text-gray-500">({profile.name})</span>}
            </h2>
            {data?.generated_at && (
              <p className="mt-0.5 text-[11px] text-gray-400">
                Frozen {readDate(data.generated_at)} (version {data.version}) - the list does not change during the month.
                {data.durable === false && <span className="ml-1 font-semibold text-amber-700">Not durable yet.</span>}
              </p>
            )}
          </div>
          {isAdmin && (
            <button onClick={() => { if (window.confirm("Build a new version of this month's list? The current one is kept.")) load(true); }}
              disabled={loading}
              className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50">
              Regenerate
            </button>
          )}
        </div>
        {bad.length > 0 && (
          <div className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-[11px] text-red-700">
            <span className="font-semibold">Built with stale inputs: </span>
            {bad.map(([k, v]) => `${INPUT_LABEL[k] ?? k} (${v.status}${v.detail ? ` - ${v.detail}` : ""})`).join("; ")}.
            Names those inputs would have added are missing from this list.
          </div>
        )}
      </div>

      {error && (
        <div className="shrink-0 border-b border-red-200 bg-red-50 px-6 py-2.5 text-sm text-red-700">
          <span className="font-semibold">Error: </span>{error}
        </div>
      )}

      {loading && !data && (
        <div className="flex flex-1 items-center justify-center text-sm text-gray-500">
          Building this month's list from the queue, Macro and the news archive - about a minute the first time...
        </div>
      )}

      {data && (
        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
          {shownBds.map((b) => {
            const mine = rows.filter((r) => r.bd_id === b.id);
            return (
              <section key={b.id} className="rounded-xl border border-gray-200 bg-white">
                <header className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
                  <h3 className="text-sm font-bold text-gray-900">{b.name}</h3>
                  <span className="text-[11px] text-gray-400">{mine.length} name{mine.length === 1 ? "" : "s"}</span>
                </header>
                {mine.length === 0 ? (
                  <p className="px-5 py-3 text-xs text-gray-500">
                    {bad.length ? "No names - inputs were stale when the list was built (see above)."
                      : "No names assigned: every input answered and there were not enough qualifying signals this month."}
                  </p>
                ) : (
                  <ul className="divide-y divide-gray-100">
                    {mine.map((r) => <Row key={r.key} row={r} owner={b.id} />)}
                  </ul>
                )}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
