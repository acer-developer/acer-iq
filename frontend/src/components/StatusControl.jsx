import React, { useState } from "react";
import { apiFetch } from "../lib/auth.js";

// The BD's one click (operator 2026-09-29: statuses, not stages).
// Pending / In progress / Closed; Closed forces Won or Lost (month-end outcomes
// need it - Head of BD); Lost may carry a reason; a one-line note is optional.
// Who changed it and when is stamped by the server.

export const LOST_REASONS = ["Price", "TAT", "Went to other agency", "Issuer deferred", "No response"];

export const STATUS_STYLE = {
  Pending: "border-gray-200 bg-gray-50 text-gray-600",
  "In progress": "border-blue-200 bg-blue-50 text-blue-700",
  Won: "border-emerald-200 bg-emerald-50 text-emerald-700",
  Lost: "border-red-200 bg-red-50 text-red-700",
};

async function post(path, body) {
  const res = await apiFetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(res.status === 401 ? "sign in first" : data.detail ?? `HTTP ${res.status}`);
  return data;
}

// `ensureSaved` (optional) puts the company in the pipeline first - a name on
// This Month's Leads is only saved when a BD first touches it.
export default function StatusControl({ companyName, status = "Pending", readOnly, ensureSaved, onChanged }) {
  const [closing, setClosing] = useState(false);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [current, setCurrent] = useState(status);

  React.useEffect(() => { setCurrent(status); }, [status]);

  const send = async (next) => {
    setBusy(true);
    setError("");
    try {
      if (ensureSaved) await ensureSaved();
      await post(`/api/leads/${encodeURIComponent(companyName)}/status`,
                 { status: next, note, lost_reason: next === "Lost" ? reason || null : null });
      setCurrent(next);
      setClosing(false);
      setNote("");
      onChanged?.(next);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const shown = current === "Won" || current === "Lost" ? `Closed - ${current}` : current;
  if (readOnly) {
    return <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${STATUS_STYLE[current] ?? STATUS_STYLE.Pending}`}>{shown}</span>;
  }

  return (
    <div className="text-right">
      <select
        value={closing ? "Closed" : (current === "Won" || current === "Lost" ? "Closed" : current)}
        disabled={busy}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "Closed") { setClosing(true); return; }
          setClosing(false);
          if (v !== current) send(v);
        }}
        className={`rounded-lg border px-2 py-1 text-xs font-semibold ${STATUS_STYLE[current] ?? STATUS_STYLE.Pending}`}
      >
        <option value="Pending">Pending</option>
        <option value="In progress">In progress</option>
        <option value="Closed">{current === "Won" || current === "Lost" ? `Closed - ${current}` : "Closed"}</option>
      </select>
      {closing && (
        <div className="mt-1.5 flex flex-col items-end gap-1">
          <div className="flex gap-1">
            <button disabled={busy} onClick={() => send("Won")}
              className="rounded-md border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[11px] font-bold text-emerald-700">Won</button>
            <button disabled={busy} onClick={() => send("Lost")}
              className="rounded-md border border-red-300 bg-red-50 px-2 py-0.5 text-[11px] font-bold text-red-700">Lost</button>
          </div>
          <select value={reason} onChange={(e) => setReason(e.target.value)}
            className="rounded-md border border-gray-200 px-1.5 py-0.5 text-[11px] text-gray-600">
            <option value="">Lost reason (optional)</option>
            {LOST_REASONS.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
      )}
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)"
        className="mt-1 w-40 rounded-md border border-gray-200 px-1.5 py-0.5 text-[11px]" />
      {error && <p className="mt-1 max-w-[12rem] text-[10px] text-red-600">{error}</p>}
    </div>
  );
}
