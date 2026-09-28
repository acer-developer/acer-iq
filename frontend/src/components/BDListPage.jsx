import React, { useState, useEffect, useCallback } from "react";
import { apiFetch } from "../lib/auth.js";
import { safeUrl } from "../lib/safeUrl.js";
import { useProfile } from "../lib/profile.js";
import StatusControl, { STATUS_STYLE } from "./StatusControl.jsx";

// Same folding as backend pipeline_store.norm_name, so a list name and its
// saved lead meet on one key.
export const fold = (n) => (n || "").toUpperCase().replace(/\s+/g, " ").trim()
  .replace(/ (PRIVATE LIMITED|PVT LTD|PVT\. LTD\.|LIMITED|LTD\.|LTD)$/, "").replace(/[ .,-]+$/, "");
const STALE = { Pending: 7, "In progress": 14 };
const daysSince = (iso) => (iso ? Math.floor((Date.now() - new Date(iso).getTime()) / 86400000) : null);
const isStale = (st, updated) => STALE[st] != null && daysSince(updated) > STALE[st];

// Tab 2 - the monthly BD list (backend/pipeline/bd_list.py). Generated once a
// month and frozen; Admin sees all four BDs, a BD profile sees only theirs.

function readDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? String(iso).slice(0, 10)
    : d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

const INPUT_LABEL = { queue: "Rating-action queue", macro: "Macro", news: "Company news",
                      refinance: "Debt maturities (BSE)", pipeline: "ACER history (pipeline)" };

async function addToPipeline(row, owner) {
  const res = await apiFetch("/api/leads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      company_name: row.company_name, winnability: row.winnability,
      flags: { bd_list: true, signals: row.signals, instrument: row.instrument,
               trigger: row.trigger, urgency: row.urgency },
      agencies_seen: [], owner, cin: row.cin && row.cin !== "CIN not found" ? row.cin : undefined,
      origin: "list", reason: row.reason,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(res.status === 401 ? "sign in to save leads" : body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

const URGENCY = { "This week": "border-red-200 bg-red-50 text-red-700", "This month": "border-gray-200 bg-gray-50 text-gray-600" };

function Fact({ label, value }) {
  const gap = /not known|not visible|not found|unavailable|not sourced|none listed|No ACER history/i.test(value ?? "");
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wide text-gray-400">{label}</dt>
      <dd className={`truncate text-xs ${gap ? "text-gray-400 italic" : "text-gray-800"}`} title={value}>{value}</dd>
    </div>
  );
}

function Row({ row, owner, lead, onChanged, readOnly }) {
  return (
    <li className="px-5 py-3">
      <div className="flex items-start gap-3">
        <span className="w-6 shrink-0 pt-0.5 text-right font-mono text-xs text-gray-400">{row.rank}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-gray-900">{row.company_name}</span>
            <span className="font-mono text-[10px] text-gray-400">{row.cin}</span>
            <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[10px] font-bold text-blue-700"
              title={row.instrument_detail}>{row.instrument}</span>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold ${URGENCY[row.urgency] ?? URGENCY["This month"]}`}>{row.urgency}</span>
            <span className="rounded-full border border-gray-200 px-2 py-0.5 text-[10px] text-gray-500">{row.trigger.replace("_", " ")}</span>
            {row.carried_over && <span className="rounded-full border border-purple-200 bg-purple-50 px-2 py-0.5 text-[10px] font-bold text-purple-700">Carried over</span>}
            {row.in_progress && <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">In progress: {row.in_progress}</span>}
            <span className="text-[10px] text-gray-400">score {row.score} · winnability {row.winnability ?? "?"}</span>
          </div>
          <p className="mt-1 text-xs text-gray-700">{row.reason}</p>
          <p className="mt-0.5 text-xs font-medium text-gray-800"><span className="text-blue-600">Play: </span>{row.play}</p>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 md:grid-cols-4">
            <Fact label={`Segment${row.segment_inferred ? " (inferred from name)" : ""}`} value={row.segment} />
            <Fact label="Size" value={row.size_cr} />
            <Fact label="Current agency" value={row.current_agency} />
            <Fact label="Latest rating" value={row.latest_rating} />
            <Fact label="Debt maturity" value={row.maturity_date} />
            <Fact label="Contact route" value={row.contact_route} />
            <Fact label="ACER history" value={row.acer_history} />
          </dl>
          <p className="mt-1.5 text-[11px] text-gray-400">
            {row.sources.map((s, i) => (
              <span key={i}>
                {i > 0 && " · "}
                {safeUrl(s.url) ? <a href={safeUrl(s.url)} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">{s.label}</a> : s.label}
                {s.read_at && <> read {readDate(s.read_at)}</>}
              </span>
            ))}
          </p>
        </div>
        <div className="shrink-0">
          <StatusControl companyName={row.company_name} status={lead?.status ?? "Pending"}
            readOnly={readOnly} ensureSaved={lead ? null : () => addToPipeline(row, owner)}
            onChanged={onChanged} />
          {lead?.updated_at && (
            <p className={`mt-1 text-right text-[10px] ${isStale(lead.status, lead.updated_at) ? "font-semibold text-red-700" : "text-gray-400"}`}>
              updated {readDate(lead.updated_at)}{isStale(lead.status, lead.updated_at) && " - stale"}
            </p>
          )}
        </div>
      </div>
    </li>
  );
}

function AdminSummary({ rows, bds, leads, onPick, generatedAt }) {
  return (
    <div className="grid gap-3 md:grid-cols-4">
      {bds.map((b) => {
        const mine = rows.filter((r) => r.bd_id === b.id && !r.in_progress);
        const st = (r) => leads[r.key]?.status ?? "Pending";
        const counts = { Pending: 0, "In progress": 0, Won: 0, Lost: 0 };
        for (const r of rows.filter((x) => x.bd_id === b.id)) counts[st(r)] += 1;
        const touched = mine.filter((r) => st(r) !== "Pending").length;
        // A name nobody has touched is Pending since the list was generated.
        const stale = rows.filter((r) => r.bd_id === b.id)
          .filter((r) => isStale(st(r), leads[r.key]?.updated_at ?? generatedAt)).length;
        return (
          <button key={b.id} onClick={() => onPick(b.id)}
            className="rounded-xl border border-gray-200 bg-white p-3 text-left hover:border-blue-300">
            <div className="flex items-center justify-between">
              <span className="text-sm font-bold text-gray-900">{b.name}</span>
              <span className="text-[11px] text-gray-400">{mine.length ? Math.round((100 * touched) / mine.length) : 0}% touched</span>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1 text-[11px]">
              {Object.entries(counts).map(([k, v]) => (
                <span key={k} className={`rounded-full border px-1.5 py-0.5 ${STATUS_STYLE[k]}`}>{k} {v}</span>
              ))}
            </div>
            <p className="mt-1.5 text-[11px] text-gray-500">
              Won {counts.Won} · <span className={stale ? "font-semibold text-red-700" : ""}>{stale} stale</span>
            </p>
          </button>
        );
      })}
    </div>
  );
}

function AllBDsTable({ rows, bds, leads, selfSourced }) {
  const name = (id) => bds.find((b) => b.id === id)?.name ?? (id || "Unassigned");
  const all = [...rows.map((r) => ({ ...r, lead: leads[r.key] })),
               ...selfSourced.map((l) => ({ key: fold(l.company_name), bd_id: l.owner, rank: "-",
                 company_name: l.company_name, instrument: l.flags?.instrument ?? "-", urgency: "-",
                 trigger: "self-sourced", reason: l.notes, lead: l, self: true }))];
  return (
    <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white">
      <table className="w-full text-left text-xs">
        <thead className="border-b border-gray-100 text-[10px] uppercase tracking-wide text-gray-400">
          <tr>
            <th className="px-4 py-2">BD</th><th className="py-2 pr-3">#</th><th className="pr-3">Company</th>
            <th className="pr-3">Instrument</th><th className="pr-3">Urgency</th><th className="pr-3">Trigger</th>
            <th className="pr-3">Status</th><th className="pr-3">Last updated</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {all.map((r) => {
            const st = r.lead?.status ?? "Pending";
            const stale = r.lead && isStale(st, r.lead.updated_at);
            return (
              <tr key={`${r.bd_id}-${r.key}`} className="align-top">
                <td className="px-4 py-1.5 font-semibold text-gray-900">{name(r.bd_id)}</td>
                <td className="py-1.5 pr-3 text-gray-400">{r.rank}</td>
                <td className="pr-3 text-gray-900" title={r.reason}>{r.company_name}
                  {r.self && <span className="ml-1 rounded-full border border-emerald-200 bg-emerald-50 px-1.5 text-[10px] text-emerald-700">self-sourced</span>}
                  {r.in_progress && <span className="ml-1 text-emerald-700">(carried, in progress)</span>}
                  {r.carried_over && <span className="ml-1 text-purple-700">(carried over)</span>}</td>
                <td className="pr-3">{r.instrument}</td>
                <td className={`pr-3 ${r.urgency === "This week" ? "font-semibold text-red-700" : ""}`}>{r.urgency}</td>
                <td className="pr-3">{String(r.trigger).replace("_", " ")}</td>
                <td className="pr-3">
                  <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${STATUS_STYLE[st]}`}>
                    {st === "Won" || st === "Lost" ? `Closed - ${st}` : st}
                  </span>
                </td>
                <td className={`pr-3 ${stale ? "font-semibold text-red-700" : "text-gray-500"}`}>
                  {r.lead?.updated_at ? `${readDate(r.lead.updated_at)}${stale ? " (stale)" : ""}` : "not touched"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function BDListPage() {
  const { profile, bds, isAdmin } = useProfile();
  const [adminView, setAdminView] = useState("all");
  const [leads, setLeads] = useState({});          // fold(name) -> saved lead
  const [selfSourced, setSelfSourced] = useState([]);
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

  // Where each of this month's names stands: Admin sees the team, a BD their own.
  const loadLeads = useCallback(() => {
    const q = isAdmin ? "?scope=all" : profile.id ? `?scope=all&owner=${encodeURIComponent(profile.id)}` : "?scope=mine";
    apiFetch(`/api/leads${q}`).then((r) => (r.ok ? r.json() : { leads: [] })).then((d) => {
      const m = {};
      for (const l of d.leads ?? []) m[fold(l.company_name)] = l;
      setLeads(m);
      setSelfSourced((d.leads ?? []).filter((l) => l.origin === "self_sourced"));
    }).catch(() => {});
  }, [isAdmin, profile.id]);
  useEffect(() => { loadLeads(); }, [loadLeads, data]);

  const rows = data?.rows ?? [];
  const shownBds = isAdmin
    ? (adminView === "all" ? bds : bds.filter((b) => b.id === adminView))
    : bds.filter((b) => b.id === profile.id);
  const bad = Object.entries(data?.inputs ?? {}).filter(([k, v]) => !k.startsWith("_") && v.status !== "ok");
  const fill = data?.inputs?._fill ?? {};

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-gray-50">
      <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-bold text-gray-900">
              This Month's Leads {data?.month && <span className="font-normal text-gray-500">- {data.month}</span>}
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
        {isAdmin && data && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {[{ id: "all", name: `All BDs (${rows.length})` },
              ...bds.map((b) => ({ id: b.id, name: `${b.name} (${rows.filter((r) => r.bd_id === b.id).length})` }))].map((o) => (
              <button key={o.id} onClick={() => setAdminView(o.id)}
                className={`rounded-lg px-3 py-1 text-xs font-medium ${adminView === o.id ? "bg-gray-900 text-white" : "border border-gray-200 bg-gray-50 text-gray-600 hover:bg-gray-100"}`}>
                {o.name}
              </button>
            ))}
          </div>
        )}
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
          {isAdmin && adminView === "all" && (
            <>
              <AdminSummary rows={rows} bds={bds} leads={leads} onPick={setAdminView} generatedAt={data.generated_at} />
              <AllBDsTable rows={rows} bds={bds} leads={leads} selfSourced={selfSourced} />
            </>
          )}
          {!(isAdmin && adminView === "all") && shownBds.map((b) => {
            const mine = rows.filter((r) => r.bd_id === b.id);
            return (
              <section key={b.id} className="rounded-xl border border-gray-200 bg-white">
                <header className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
                  <div>
                    <h3 className="text-sm font-bold text-gray-900">{b.name}</h3>
                    {b.segment && <p className="text-[11px] text-gray-400">{b.segment}</p>}
                  </div>
                  <span className={`text-[11px] ${fill[b.id]?.note ? "font-semibold text-amber-700" : "text-gray-400"}`}>
                    {fill[b.id]?.note || `${fill[b.id]?.filled ?? mine.length} of ${fill[b.id]?.of ?? 10} new names`}
                  </span>
                </header>
                {mine.length === 0 ? (
                  <p className="px-5 py-3 text-xs text-gray-500">
                    {bad.length ? "No names - inputs were stale when the list was built (see above)."
                      : "No names assigned: every input answered and there were not enough qualifying signals this month."}
                  </p>
                ) : (
                  <ul className="divide-y divide-gray-100">
                    {mine.map((r) => (
                      <Row key={r.key} row={r} owner={b.id} lead={leads[r.key]} onChanged={loadLeads}
                        readOnly={leads[r.key]?.mine === false} />
                    ))}
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
