import React, { useState, useEffect, useCallback } from "react";
import { apiUrl } from "../lib/api.js";
import { apiFetch } from "../lib/auth.js";
import { EmptyState, FreshnessStrip } from "./SourceFreshness.jsx";
import { useProfile } from "../lib/profile.js";

// Saves to the signed-in BD's own pipeline (per-user via Supabase; the shared
// SQLite list only when auth is not configured). The save is idempotent
// server-side, so clicking Add on a company already at Proposal will not reset
// it to Identified.
async function saveLead(lead, owner) {
  const res = await apiFetch("/api/leads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      company_name: lead.company_name,
      winnability: lead.winnability,
      flags: lead.flags,
      agencies_seen: lead.agencies_seen,
      owner: owner || "",
      origin: "list",
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(res.status === 401 ? "sign in to save leads" : body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

const FLAG_META = {
  first_timer:    { label: "First-timer",      color: "bg-blue-50 text-blue-700 border-blue-200" },
  inc_tagged:     { label: "INC-tagged",       color: "bg-rose-50 text-rose-700 border-rose-200" },
  self_withdrawn: { label: "Self-withdrawn",   color: "bg-purple-50 text-purple-700 border-purple-200" },
  multi_cra:      { label: "Multi-CRA",        color: "bg-amber-50 text-amber-700 border-amber-200" },
};

function SignalChips({ flags, reasons }) {
  const active = Object.keys(FLAG_META).filter((k) => flags?.[k]);
  if (!active.length) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {active.map((k) => (
        <span
          key={k}
          title={reasons?.find((r) => r) || FLAG_META[k].label}
          className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-bold ${FLAG_META[k].color}`}
        >
          {FLAG_META[k].label}
        </span>
      ))}
    </div>
  );
}

function WinnabilityBadge({ lead }) {
  if (lead.blocked) {
    return (
      <div className="flex h-11 w-11 shrink-0 flex-col items-center justify-center rounded-lg border-2 border-red-300 bg-red-50">
        <svg className="h-5 w-5 text-red-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
        </svg>
      </div>
    );
  }
  const color =
    lead.winnability >= 70 ? "text-red-500 border-red-200 bg-red-50" :
    lead.winnability >= 40 ? "text-orange-500 border-orange-200 bg-orange-50" :
    "text-gray-400 border-gray-200 bg-gray-50";
  return (
    <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border ${color}`}>
      <span className="text-base font-bold">{lead.winnability}</span>
    </div>
  );
}

function QueueRow({ lead, onAdd, saved, busy, addError }) {
  const rowCls = lead.blocked
    ? "bg-white border-l-4 border-l-red-400"
    : lead.suppressed
      ? "bg-gray-50/60 opacity-60"
      : "bg-white hover:bg-gray-50";

  return (
    <div className={`flex items-start gap-4 border-b border-gray-100 px-6 py-4 transition ${rowCls}`}>
      <WinnabilityBadge lead={lead} />

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`text-sm font-bold ${lead.suppressed ? "text-gray-500" : "text-gray-900"}`}>
            {lead.company_name}
          </span>
          {lead.suppressed && (
            <span className="inline-flex rounded-full border border-gray-300 bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-500">
              LOW WINNABILITY
            </span>
          )}
          {lead.blocked && (
            <span className="inline-flex rounded-full border border-red-300 bg-red-100 px-2 py-0.5 text-[10px] font-bold text-red-700">
              BLOCKED - DO NOT CALL YET
            </span>
          )}
        </div>

        <p className="mt-0.5 text-xs text-gray-500">
          {[lead.latest_rating, lead.latest_action, lead.latest_date]
            .filter(Boolean).join(" · ")}
          {lead.agencies_seen?.length ? ` · ${lead.agencies_seen.join(", ")}` : ""}
        </p>

        {lead.blocked ? (
          <p className="mt-1.5 text-xs font-medium text-red-700">{lead.credit_screen?.reason}</p>
        ) : (
          <SignalChips flags={lead.flags} reasons={lead.reasons} />
        )}
        {addError && (
          <p className="mt-1.5 text-xs font-medium text-red-700">
            Could not save: {addError}
          </p>
        )}
      </div>

      <button
        onClick={() => onAdd(lead)}
        disabled={lead.blocked || saved || busy}
        className="shrink-0 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600
          transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
        title={lead.blocked ? "Blocked leads can't be added"
                : saved ? "Already in the pipeline" : "Add to the pipeline"}
      >
        {saved ? "\u2713 Saved" : busy ? "Saving\u2026" : "+ Add"}
      </button>
    </div>
  );
}

export default function QueuePage() {
  const { profile, isAdmin } = useProfile();
  const [leads, setLeads] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [coverage, setCoverage] = useState(null);
  const [saved, setSaved] = useState(() => new Set());
  const [busy, setBusy] = useState(() => new Set());
  const [addErrors, setAddErrors] = useState({});

  const handleAdd = useCallback(async (lead) => {
    const name = lead.company_name;
    setBusy((b) => new Set(b).add(name));
    setAddErrors((e) => ({ ...e, [name]: undefined }));
    try {
      // Saved to the profile in view; Admin saves unassigned (reassign later).
      await saveLead(lead, isAdmin ? "" : profile.id);
      setSaved((s) => new Set(s).add(name));
    } catch (err) {
      // Never fail silently: someone would believe a lead is tracked when it
      // is not, and act on that belief.
      setAddErrors((e) => ({ ...e, [name]: err.message }));
    } finally {
      setBusy((b) => {
        const next = new Set(b);
        next.delete(name);
        return next;
      });
    }
  }, [isAdmin, profile.id]);

  const [freshness, setFreshness] = useState({ rows: [], verdict: null });

  const fetchQueue = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(apiUrl("/api/queue"));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setLeads(data.leads ?? []);
      setCoverage(data.coverage ?? null);
      setFreshness({ rows: data.freshness ?? [], verdict: data.empty_means ?? null });
      try {
        // Team-wide, so a name another BD already holds shows as saved.
        const savedRes = await apiFetch("/api/leads?scope=all");
        if (savedRes.ok) {
          const s = await savedRes.json();
          setSaved(new Set((s.leads ?? []).map((l) => l.company_name)));
        }
      } catch {
        // The queue is still usable if the pipeline list is unavailable;
        // rows just show as unsaved rather than the page failing.
      }
    } catch (e) {
      // "Couldn't reach the queue" must never render as "no leads today".
      setLeads([]);
      setCoverage(null);
      setError(`Queue unavailable: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchQueue(); }, [fetchQueue]);

  const workable = leads.filter((l) => !l.blocked && !l.suppressed);
  const blocked = leads.filter((l) => l.blocked);
  const suppressed = leads.filter((l) => l.suppressed && !l.blocked);
  const sorted = [...leads].sort((a, b) => (b.winnability ?? 0) - (a.winnability ?? 0));

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-gray-50">

      {/* Header */}
      <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold text-gray-900">Ranked Queue</h2>
          <button
            onClick={fetchQueue}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 transition hover:bg-gray-50 disabled:opacity-50"
          >
            <svg className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Refresh
          </button>
        </div>

        {/* Coverage is not decoration: five of seven CRAs cannot be read, so a
            short queue may mean thin sources rather than a quiet week. */}
        {coverage && (
          <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-700">
            <span className="font-semibold">Coverage:</span> {coverage.note}
          </div>
        )}
        {freshness.rows.length > 0 && (
          <div className="mt-2"><FreshnessStrip rows={freshness.rows} /></div>
        )}

        {/* Summary metrics */}
        <div className="mt-3 grid grid-cols-4 gap-3">
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
            <div className="text-lg font-bold text-gray-900">{leads.length}</div>
            <div className="text-[10px] uppercase tracking-wide text-gray-400">Total in queue</div>
          </div>
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
            <div className="text-lg font-bold text-emerald-600">{workable.length}</div>
            <div className="text-[10px] uppercase tracking-wide text-gray-400">Workable</div>
          </div>
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
            <div className="text-lg font-bold text-gray-400">{suppressed.length}</div>
            <div className="text-[10px] uppercase tracking-wide text-gray-400">Low winnability</div>
          </div>
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
            <div className="text-lg font-bold text-red-600">{blocked.length}</div>
            <div className="text-[10px] uppercase tracking-wide text-gray-400">Blocked (credit screen)</div>
          </div>
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
            <p className="mt-3 text-sm text-gray-500">Loading ranked queue...</p>
          </div>
        </div>
      )}

      {!loading && !error && sorted.length === 0 && (
        <div className="flex flex-1 items-center justify-center">
          <EmptyState
            verdict={freshness.verdict}
            quietTitle="No rating actions in this window"
            quietText="Every agency feed answered and none named a new issuer - a quiet window, not a broken one."
          />
        </div>
      )}

      {!loading && sorted.length > 0 && (
        <div className="flex-1 overflow-y-auto">
          {sorted.map((lead) => (
            <QueueRow
              key={lead.company_name}
              lead={lead}
              onAdd={handleAdd}
              saved={saved.has(lead.company_name)}
              busy={busy.has(lead.company_name)}
              addError={addErrors[lead.company_name]}
            />
          ))}
        </div>
      )}
    </div>
  );
}
