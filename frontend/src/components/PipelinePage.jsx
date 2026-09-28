import React, { useState, useEffect, useCallback } from "react";
import { apiUrl } from "../lib/api.js";
import { apiFetch, authConfigured, openAuthed } from "../lib/auth.js";
import { useProfile } from "../lib/profile.js";
import StatusControl, { STATUS_STYLE } from "./StatusControl.jsx";

// Statuses, not stages (operator 2026-09-29; BD_LIST_SPEC.md section 3).
const GROUPS = ["Pending", "In progress", "Won", "Lost"];
const GROUP_LABEL = { Pending: "Pending", "In progress": "In progress", Won: "Closed - Won", Lost: "Closed - Lost" };
const STALE = { Pending: 7, "In progress": 14 };
const staleBy = (l) => {
  const lim = STALE[l.status];
  if (!lim || !l.updated_at) return 0;
  const d = Math.floor((Date.now() - new Date(l.updated_at).getTime()) / 86400000);
  return d > lim ? d - lim : 0;
};

// The pipeline, built to BD_LIST_SPEC.md sections 3-4 (section 3 as
// superseded by the operator: three statuses, one click, nothing mandatory
// except Won/Lost when closing; staleness instead of follow-up dates).
// - Admin sees every BD's leads plus the team screen; a BD profile sees only
//   theirs. Rows another signed-in user owns are read-only: RLS would refuse
//   the write, so the UI does not offer it.

const FLAG_META = {
  first_timer:    { label: "First-timer",    color: "bg-blue-50 text-blue-700 border-blue-200" },
  inc_tagged:     { label: "INC-tagged",     color: "bg-rose-50 text-rose-700 border-rose-200" },
  self_withdrawn: { label: "Self-withdrawn", color: "bg-purple-50 text-purple-700 border-purple-200" },
  multi_cra:      { label: "Multi-CRA",      color: "bg-amber-50 text-amber-700 border-amber-200" },
};


async function jsonOrThrow(res) {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error(authConfigured ? "sign in again - your session has expired"
        : "the server keeps a pipeline per BD, but sign-in is not configured on this deploy "
          + "(set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in Vercel)");
    }
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return body;
}

function EventLog({ companyName }) {
  const [events, setEvents] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    apiFetch(`/api/leads/events?company_name=${encodeURIComponent(companyName)}`)
      .then(jsonOrThrow)
      .then((d) => { if (!cancelled) setEvents(d.events ?? []); })
      .catch((e) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [companyName]);
  if (error) return <p className="mt-2 text-xs font-medium text-red-700">Could not load history: {error}</p>;
  if (!events) return <p className="mt-2 text-xs text-gray-400">Loading history...</p>;
  if (!events.length) return <p className="mt-2 text-xs text-gray-400">No history visible to you.</p>;
  return (
    <ul className="mt-2 space-y-1 border-t border-gray-100 pt-2">
      {events.map((e, i) => (
        <li key={e.id ?? i} className="text-xs text-gray-500">
          <span className="font-semibold text-gray-700">{e.event}</span>
          {e.detail && <span> - {e.detail}</span>}
          <span className="ml-1.5 text-gray-400">{new Date(e.at).toLocaleString()}</span>
        </li>
      ))}
    </ul>
  );
}

function Reassign({ lead, bds, onDone }) {
  const [owner, setOwner] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const go = async () => {
    if (!owner || reason.trim().length < 5) { setError("Pick a BD and give a reason"); return; }
    try {
      await jsonOrThrow(await apiFetch(`/api/leads/${encodeURIComponent(lead.company_name)}/reassign`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ owner, reason }),
      }));
      onDone();
    } catch (e) { setError(e.message); }
  };
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-gray-100 pt-3">
      <span className="text-xs text-gray-500">Reassign to</span>
      <select value={owner} onChange={(e) => setOwner(e.target.value)} className="rounded-lg border border-gray-200 px-2 py-1 text-xs">
        <option value="">Choose BD...</option>
        {bds.filter((b) => b.id !== lead.owner).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
      </select>
      <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (logged)"
        className="min-w-[12rem] flex-1 rounded-lg border border-gray-200 px-2 py-1 text-xs" />
      <button onClick={go} className="rounded-lg bg-gray-900 px-3 py-1 text-xs font-medium text-white">Reassign</button>
      {error && <span className="text-xs text-red-700">{error}</span>}
    </div>
  );
}

function LeadCard({ lead, schema, bds, isAdmin, onChanged }) {
  const [panel, setPanel] = useState("");
  const [error, setError] = useState("");
  const ownerName = bds.find((b) => b.id === lead.owner)?.name ?? (lead.owner || "Unassigned");
  const readOnly = lead.mine === false;
  const current = (lead.stage_details ?? {})[lead.stage];

  const remove = async () => {
    if (!window.confirm(`Remove ${lead.company_name}? Its history is kept.`)) return;
    try {
      await jsonOrThrow(await apiFetch(`/api/leads/${encodeURIComponent(lead.company_name)}`, { method: "DELETE" }));
      onChanged();
    } catch (e) { setError(e.message); }
  };

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex items-start gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-gray-200 bg-gray-50">
          <span className="text-base font-bold text-gray-700">{lead.winnability ?? "-"}</span>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <button className="text-left text-sm font-bold text-gray-900 hover:underline"
              onClick={() => setPanel(panel === "history" ? "" : "history")}>
              {lead.company_name}
            </button>
            <span className="rounded-full border border-gray-200 px-2 py-0.5 text-[10px] text-gray-600">{ownerName}</span>
            {lead.origin === "self_sourced" && (
              <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">Self-sourced</span>
            )}
            {lead.cin && <span className="font-mono text-[10px] text-gray-400">{lead.cin}</span>}
          </div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {Object.keys(FLAG_META).filter((k) => lead.flags?.[k]).map((k) => (
              <span key={k} className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-bold ${FLAG_META[k].color}`}>{FLAG_META[k].label}</span>
            ))}
          </div>
          <p className={`mt-1 text-[11px] ${staleBy(lead) ? "font-semibold text-red-700" : "text-gray-400"}`}>
            {lead.updated_at && `Updated ${new Date(lead.updated_at).toLocaleDateString("en-IN")}`}
            {current?.changed_by && ` by ${current.changed_by}`}
            {staleBy(lead) > 0 && ` - stale (${staleBy(lead)} days over)`}
            {current?.lost_reason && ` - lost: ${current.lost_reason}`}
          </p>
          {lead.notes && <p className="mt-1.5 rounded-md bg-gray-50 px-2 py-1 text-xs text-gray-600">{lead.notes}</p>}
          {readOnly && <p className="mt-1 text-[10px] text-gray-400">Another BD's lead - read-only for you.</p>}
          {error && <p className="mt-1.5 text-xs font-medium text-red-700">{error}</p>}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <button onClick={() => openAuthed(`/api/brief/${encodeURIComponent(lead.company_name)}`)}
            className="rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50">Brief</button>
          <StatusControl companyName={lead.company_name} status={lead.status} readOnly={readOnly}
            onChanged={onChanged} />
          {isAdmin && (
            <button onClick={() => setPanel(panel === "reassign" ? "" : "reassign")}
              className="rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50">Reassign</button>
          )}
          {!readOnly && (
            <button onClick={remove}
              className="rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-red-500 hover:bg-red-50">Remove</button>
          )}
        </div>
      </div>
      {panel === "reassign" && <Reassign lead={lead} bds={bds} onDone={() => { setPanel(""); onChanged(); }} />}
      {panel === "history" && <EventLog companyName={lead.company_name} />}
    </div>
  );
}

function AddSelfSourced({ bds, profile, isAdmin, onDone }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ company_name: "", cin: "", reason: "", owner: isAdmin ? "" : profile.id });
  const [error, setError] = useState("");
  const save = async () => {
    if (!f.company_name.trim() || !f.owner) { setError("Company and BD are required"); return; }
    try {
      await jsonOrThrow(await apiFetch("/api/leads", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...f, origin: "self_sourced", flags: {}, agencies_seen: [] }),
      }));
      setOpen(false);
      setF({ company_name: "", cin: "", reason: "", owner: isAdmin ? "" : profile.id });
      onDone();
    } catch (e) { setError(e.message); }
  };
  if (!open) {
    return <button onClick={() => setOpen(true)} className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50">+ Self-sourced lead</button>;
  }
  const inp = "rounded-lg border border-gray-200 px-2 py-1 text-xs";
  return (
    <div className="mt-3 flex flex-wrap items-end gap-2 rounded-lg border border-gray-200 bg-gray-50 p-3">
      <input className={inp} placeholder="Company name *" value={f.company_name} onChange={(e) => setF({ ...f, company_name: e.target.value })} />
      <input className={`${inp} font-mono`} placeholder="CIN * (21 characters)" value={f.cin} onChange={(e) => setF({ ...f, cin: e.target.value.toUpperCase() })} />
      <input className={`${inp} min-w-[18rem] flex-1`} placeholder="Why it matters to ACER (one sentence) *" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} />
      {isAdmin && (
        <select className={inp} value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })}>
          <option value="">BD *</option>
          {bds.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
      )}
      <button onClick={save} className="rounded-lg bg-blue-600 px-3 py-1 text-xs font-medium text-white">Save</button>
      <button onClick={() => setOpen(false)} className="rounded-lg border border-gray-200 px-3 py-1 text-xs text-gray-500">Cancel</button>
      {error && <p className="w-full text-xs text-red-700">{error}</p>}
    </div>
  );
}

function TeamSummary() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    apiFetch("/api/pipeline/team").then(jsonOrThrow).then(setData).catch((e) => setError(e.message));
  }, []);
  if (error) return <p className="mt-3 text-xs text-red-700">Team view unavailable: {error}</p>;
  if (!data) return <p className="mt-3 text-xs text-gray-400">Loading team view...</p>;
  return (
    <div className="mt-3 space-y-2">
      {data.stale_inputs?.length > 0 && (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-[11px] text-red-700">
          This month's list was built with stale inputs: {data.stale_inputs.join(", ")}.
        </p>
      )}
      {data.clashes?.length > 0 && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-800">
          Clash - the same company is in two BDs' pipelines: {data.clashes.join(", ")}
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] uppercase tracking-wide text-gray-400">
            <tr>
              <th className="py-1 pr-3">BD</th><th className="pr-3">List touched</th><th className="pr-3">Pending</th>
              <th className="pr-3">In progress</th><th className="pr-3">Won</th><th className="pr-3">Lost</th>
              <th className="pr-3">Stale</th><th className="pr-3">Lost reasons</th><th className="pr-3">Self-sourced</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {Object.entries(data.bds).map(([id, b]) => (
              <tr key={id} className="align-top">
                <td className="py-1.5 pr-3 font-semibold text-gray-900">{b.name}</td>
                <td className="pr-3">{b.assigned ? `${b.touched}/${b.assigned} (${b.coverage_pct}%)` : data.list_generated ? "no names" : "list not built yet"}</td>
                {["Pending", "In progress", "Won", "Lost"].map((k) => (
                  <td key={k} className="pr-3">
                    <span className={`rounded-full border px-1.5 py-0.5 ${STATUS_STYLE[k]}`}>{b.status_counts?.[k] ?? 0}</span>
                  </td>
                ))}
                <td className="pr-3">
                  {(b.stale ?? []).length === 0 ? "none" : b.stale.slice(0, 4).map((o) => (
                    <div key={o.company_name} className="text-red-700">{o.company_name} ({o.status}, +{o.days_over}d)</div>
                  ))}
                </td>
                <td className="pr-3">{Object.entries(b.lost_reasons).map(([r, n]) => `${r} ${n}`).join(", ") || "-"}</td>
                <td className="pr-3">{b.self_sourced}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function PipelinePage() {
  const { profile, bds, isAdmin } = useProfile();
  const [ownerFilter, setOwnerFilter] = useState("all");
  const [leads, setLeads] = useState([]);
  const [funnel, setFunnel] = useState({});
  const [schema, setSchema] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(apiUrl("/api/pipeline/schema")).then((r) => r.json()).then(setSchema).catch(() => {});
  }, []);

  const fetchLeads = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      // Admin: everyone. A BD: their profile's leads. Signed in but not yet
      // mapped to a profile: just the rows their own login saved.
      const q = isAdmin ? "?scope=all"
        : profile.id ? `?scope=all&owner=${encodeURIComponent(profile.id)}` : "?scope=mine";
      const data = await jsonOrThrow(await apiFetch(`/api/leads${q}`));
      setLeads(data.leads ?? []);
      setFunnel(data.funnel ?? {});
    } catch (e) {
      // A failed fetch must never render as an empty pipeline.
      setLeads([]);
      setError(`Pipeline unavailable: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [isAdmin, profile.id]);

  useEffect(() => { fetchLeads(); }, [fetchLeads]);

  const stages = GROUPS;
  // Admin can look at one BD at a time without leaving Admin.
  const shown = !isAdmin || ownerFilter === "all" ? leads
    : leads.filter((l) => (l.owner || "") === (ownerFilter === "unassigned" ? "" : ownerFilter));
  const shownFunnel = {};
  for (const s of stages) shownFunnel[s] = shown.filter((l) => l.status === s).length;

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-gray-50">
      <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-bold text-gray-900">
            {isAdmin ? "Team Pipeline (Admin)" : `${profile.name}'s Pipeline`}
            {!isAdmin && !profile.id && (
              <span className="ml-2 text-xs font-normal text-amber-700">
                Your email is not mapped to a BD profile yet - showing only leads you saved yourself.
              </span>
            )}
          </h2>
          <div className="flex items-center gap-2">
            <AddSelfSourced bds={bds} profile={profile} isAdmin={isAdmin} onDone={fetchLeads} />
            {leads.length > 0 && (
              <button onClick={() => openAuthed("/api/brief")}
                className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50">Print briefs</button>
            )}
            <button onClick={fetchLeads} disabled={loading}
              className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50">Refresh</button>
          </div>
        </div>
        <div className="mt-3 grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.max(stages.length, 1)}, minmax(0,1fr))` }}>
          {stages.map((s) => (
            <div key={s} className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
              <div className="text-lg font-bold text-gray-900">{shownFunnel[s] ?? 0}</div>
              <div className="text-[10px] uppercase tracking-wide text-gray-400">{GROUP_LABEL[s]}</div>
            </div>
          ))}
        </div>
        {isAdmin && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {[{ id: "all", name: `All BDs (${leads.length})` },
              ...bds.map((b) => ({ id: b.id, name: `${b.name} (${leads.filter((l) => l.owner === b.id).length})` })),
              { id: "unassigned", name: `Unassigned (${leads.filter((l) => !l.owner).length})` }].map((o) => (
              <button key={o.id} onClick={() => setOwnerFilter(o.id)}
                className={`rounded-lg px-3 py-1 text-xs font-medium ${ownerFilter === o.id ? "bg-gray-900 text-white" : "border border-gray-200 bg-gray-50 text-gray-600 hover:bg-gray-100"}`}>
                {o.name}
              </button>
            ))}
          </div>
        )}
        {isAdmin && ownerFilter === "all" && <TeamSummary key={leads.length} />}
      </div>

      {error && (
        <div className="shrink-0 border-b border-red-200 bg-red-50 px-6 py-2.5 text-sm text-red-700">
          <span className="font-semibold">Error: </span>{error}
        </div>
      )}

      {loading && <div className="flex flex-1 items-center justify-center text-sm text-gray-500">Loading pipeline...</div>}

      {!loading && !error && shown.length === 0 && (
        <div className="flex flex-1 items-center justify-center text-center">
          <div>
            <p className="text-sm font-medium text-gray-700">No leads in this pipeline yet.</p>
            <p className="mt-1 text-xs text-gray-500">Add names from the BD List or the Ranked Queue, or add a self-sourced lead.</p>
          </div>
        </div>
      )}

      {!loading && shown.length > 0 && (
        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-4">
          {stages.map((stage) => {
            const items = shown.filter((l) => l.status === stage);
            return (
              <div key={stage}>
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-gray-400">
                  {GROUP_LABEL[stage]} <span className="text-gray-300">({items.length})</span>
                </h3>
                {items.length === 0 ? <p className="text-xs text-gray-300">None.</p> : (
                  <div className="space-y-2">
                    {items.map((l) => (
                      <LeadCard key={`${l.owner}-${l.company_name}`} lead={l} schema={schema}
                        bds={bds} isAdmin={isAdmin} onChanged={fetchLeads} />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
