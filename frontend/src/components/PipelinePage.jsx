import React, { useState, useEffect, useCallback } from "react";
import { apiFetch, authConfigured, openAuthed } from "../lib/auth.js";

// Reads/writes the pipeline via backend/main.py's "My Pipeline" endpoints.
// Per-user when Supabase is configured (the signed-in BD's token rides on every
// call and row level security scopes the rows); otherwise one shared list.

const FLAG_META = {
  first_timer:    { label: "First-timer",      color: "bg-blue-50 text-blue-700 border-blue-200" },
  inc_tagged:     { label: "INC-tagged",       color: "bg-rose-50 text-rose-700 border-rose-200" },
  self_withdrawn: { label: "Self-withdrawn",   color: "bg-purple-50 text-purple-700 border-purple-200" },
  multi_cra:      { label: "Multi-CRA",        color: "bg-amber-50 text-amber-700 border-amber-200" },
};

function SignalChips({ flags }) {
  const active = Object.keys(FLAG_META).filter((k) => flags?.[k]);
  if (!active.length) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {active.map((k) => (
        <span
          key={k}
          className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-bold ${FLAG_META[k].color}`}
        >
          {FLAG_META[k].label}
        </span>
      ))}
    </div>
  );
}

function WinnabilityBadge({ winnability }) {
  const color =
    winnability >= 70 ? "text-red-500 border-red-200 bg-red-50" :
    winnability >= 40 ? "text-orange-500 border-orange-200 bg-orange-50" :
    "text-gray-400 border-gray-200 bg-gray-50";
  return (
    <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border ${color}`}>
      <span className="text-base font-bold">{winnability ?? "-"}</span>
    </div>
  );
}

async function fetchEvents(companyName) {
  const res = await apiFetch(`/api/leads/events?company_name=${encodeURIComponent(companyName)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return data.events ?? [];
}

function EventLog({ companyName }) {
  const [events, setEvents] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    fetchEvents(companyName)
      .then((ev) => { if (!cancelled) setEvents(ev); })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [companyName]);

  if (loading) return <p className="mt-2 text-xs text-gray-400">Loading history...</p>;
  if (error) return <p className="mt-2 text-xs font-medium text-red-700">Could not load history: {error}</p>;
  if (!events?.length) return <p className="mt-2 text-xs text-gray-400">No history yet.</p>;

  return (
    <ul className="mt-2 space-y-1 border-t border-gray-100 pt-2">
      {events.map((e) => (
        <li key={e.id} className="text-xs text-gray-500">
          <span className="font-semibold text-gray-700">{e.event}</span>
          {e.detail && <span> - {e.detail}</span>}
          <span className="ml-1.5 text-gray-400">{new Date(e.at).toLocaleString()}</span>
        </li>
      ))}
    </ul>
  );
}

function LeadCard({ lead, stages, onMove, onRemove, moveError, removeError, busy }) {
  const [expanded, setExpanded] = useState(false);
  const [showMove, setShowMove] = useState(false);
  const [targetStage, setTargetStage] = useState(lead.stage);
  const [note, setNote] = useState("");
  const [confirmRemove, setConfirmRemove] = useState(false);

  const submitMove = () => {
    if (targetStage === lead.stage) { setShowMove(false); return; }
    onMove(lead.company_name, targetStage, note);
    setShowMove(false);
    setNote("");
  };

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex items-start gap-3">
        <WinnabilityBadge winnability={lead.winnability} />
        <div className="min-w-0 flex-1">
          <button
            className="text-left text-sm font-bold text-gray-900 hover:underline"
            onClick={() => setExpanded((v) => !v)}
          >
            {lead.company_name}
          </button>
          {lead.agencies?.length > 0 && (
            <p className="mt-0.5 text-xs text-gray-500">{lead.agencies.join(", ")}</p>
          )}
          <SignalChips flags={lead.flags} />
          {lead.notes && (
            <p className="mt-1.5 rounded-md bg-gray-50 px-2 py-1 text-xs text-gray-600">{lead.notes}</p>
          )}

          {moveError && (
            <p className="mt-1.5 text-xs font-medium text-red-700">Move failed: {moveError}</p>
          )}
          {removeError && (
            <p className="mt-1.5 text-xs font-medium text-red-700">Remove failed: {removeError}</p>
          )}

          {expanded && <EventLog companyName={lead.company_name} />}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <button
            onClick={() => openAuthed(`/api/brief/${encodeURIComponent(lead.company_name)}`)}
            className="rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-600 transition hover:bg-gray-50"
          >
            Brief
          </button>
          <button
            onClick={() => setShowMove((v) => !v)}
            disabled={busy}
            className="rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-600
              transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? "Working..." : "Move"}
          </button>
          {!confirmRemove ? (
            <button
              onClick={() => setConfirmRemove(true)}
              disabled={busy}
              className="rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-red-500
                transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Remove
            </button>
          ) : (
            <div className="flex gap-1">
              <button
                onClick={() => { onRemove(lead.company_name); setConfirmRemove(false); }}
                className="rounded-lg border border-red-300 bg-red-50 px-2 py-1 text-xs font-bold text-red-700 hover:bg-red-100"
              >
                Confirm
              </button>
              <button
                onClick={() => setConfirmRemove(false)}
                className="rounded-lg border border-gray-200 bg-white px-2 py-1 text-xs text-gray-500 hover:bg-gray-50"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      </div>

      {showMove && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-gray-100 pt-3">
          <select
            value={targetStage}
            onChange={(e) => setTargetStage(e.target.value)}
            className="rounded-lg border border-gray-200 px-2 py-1 text-xs"
          >
            {stages.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <input
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Optional note"
            className="min-w-[10rem] flex-1 rounded-lg border border-gray-200 px-2 py-1 text-xs"
          />
          <button
            onClick={submitMove}
            className="rounded-lg bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700"
          >
            Save
          </button>
        </div>
      )}
    </div>
  );
}

export default function PipelinePage() {
  const [leads, setLeads] = useState([]);
  const [funnelData, setFunnelData] = useState({});
  const [stages, setStages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(() => new Set());
  const [moveErrors, setMoveErrors] = useState({});
  const [removeErrors, setRemoveErrors] = useState({});

  const fetchLeads = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await apiFetch("/api/leads");
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(res.status !== 401 ? body.detail ?? `HTTP ${res.status}`
          : authConfigured ? "sign in again - your session has expired"
          : "the server keeps a pipeline per BD, but sign-in is not configured on this "
            + "deploy (set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in Vercel)");
      }
      const data = await res.json();
      setLeads(data.leads ?? []);
      setFunnelData(data.funnel ?? {});
      setStages(data.stages ?? []);
    } catch (e) {
      // A failed fetch must never render as "empty pipeline" - that reads as
      // "no leads saved" when really the list could not be loaded at all.
      setError(`Pipeline unavailable: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchLeads(); }, [fetchLeads]);

  const handleMove = useCallback(async (companyName, stage, note) => {
    setBusy((b) => new Set(b).add(companyName));
    setMoveErrors((e) => ({ ...e, [companyName]: undefined }));
    try {
      const res = await apiFetch(`/api/leads/${encodeURIComponent(companyName)}/stage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stage, note }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      // Reflect the move locally instead of a full reload; the funnel counts
      // are recomputed from the same updated array so they never drift out
      // of sync with what is on screen.
      setLeads((prev) => {
        const next = prev.map((l) =>
          l.company_name === companyName
            ? { ...l, stage, notes: note ? note : l.notes }
            : l
        );
        setFunnelData((prevFunnel) => {
          const counts = { ...prevFunnel };
          const moved = prev.find((l) => l.company_name === companyName);
          if (moved) {
            counts[moved.stage] = Math.max(0, (counts[moved.stage] ?? 0) - 1);
            counts[stage] = (counts[stage] ?? 0) + 1;
          }
          return counts;
        });
        return next;
      });
    } catch (e) {
      setMoveErrors((prev) => ({ ...prev, [companyName]: e.message }));
    } finally {
      setBusy((b) => {
        const next = new Set(b);
        next.delete(companyName);
        return next;
      });
    }
  }, []);

  const handleRemove = useCallback(async (companyName) => {
    setBusy((b) => new Set(b).add(companyName));
    setRemoveErrors((e) => ({ ...e, [companyName]: undefined }));
    try {
      const res = await apiFetch(`/api/leads/${encodeURIComponent(companyName)}`, { method: "DELETE" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      setLeads((prev) => {
        const removed = prev.find((l) => l.company_name === companyName);
        if (removed) {
          setFunnelData((prevFunnel) => ({
            ...prevFunnel,
            [removed.stage]: Math.max(0, (prevFunnel[removed.stage] ?? 0) - 1),
          }));
        }
        return prev.filter((l) => l.company_name !== companyName);
      });
    } catch (e) {
      setRemoveErrors((prev) => ({ ...prev, [companyName]: e.message }));
    } finally {
      setBusy((b) => {
        const next = new Set(b);
        next.delete(companyName);
        return next;
      });
    }
  }, []);

  const byStage = stages.map((stage) => ({
    stage,
    items: leads.filter((l) => l.stage === stage),
  }));

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-gray-50">

      <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold text-gray-900">My Pipeline</h2>
          <div className="flex items-center gap-2">
          {leads.length > 0 && (
            <button
              onClick={() => openAuthed("/api/brief")}
              className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 transition hover:bg-gray-50"
            >
              Print briefs
            </button>
          )}
          <button
            onClick={fetchLeads}
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

        {/* Funnel: every stage shown, even at zero */}
        <div className="mt-3 grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.max(stages.length, 1)}, minmax(0,1fr))` }}>
          {stages.map((s) => (
            <div key={s} className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
              <div className="text-lg font-bold text-gray-900">{funnelData[s] ?? 0}</div>
              <div className="text-[10px] uppercase tracking-wide text-gray-400">{s}</div>
            </div>
          ))}
        </div>
      </div>

      {error && (
        <div className="shrink-0 border-b border-red-200 bg-red-50 px-6 py-2.5 text-sm text-red-700">
          <span className="font-semibold">Error: </span>{error}
        </div>
      )}

      {loading && (
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center">
            <svg className="mx-auto h-8 w-8 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <p className="mt-3 text-sm text-gray-500">Loading pipeline...</p>
          </div>
        </div>
      )}

      {!loading && !error && leads.length === 0 && (
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center">
            <p className="text-sm font-medium text-gray-700">No leads in the pipeline yet.</p>
            <p className="mt-1 text-xs text-gray-500">Add leads from the Ranked Queue tab to start tracking them here.</p>
          </div>
        </div>
      )}

      {!loading && leads.length > 0 && (
        <div className="flex-1 overflow-y-auto px-6 py-4">
          <div className="space-y-6">
            {byStage.map(({ stage, items }) => (
              <div key={stage}>
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-gray-400">
                  {stage} <span className="text-gray-300">({items.length})</span>
                </h3>
                {items.length === 0 ? (
                  <p className="text-xs text-gray-300">No leads at this stage.</p>
                ) : (
                  <div className="space-y-2">
                    {items.map((lead) => (
                      <LeadCard
                        key={lead.company_name}
                        lead={lead}
                        stages={stages}
                        onMove={handleMove}
                        onRemove={handleRemove}
                        moveError={moveErrors[lead.company_name]}
                        removeError={removeErrors[lead.company_name]}
                        busy={busy.has(lead.company_name)}
                      />
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
