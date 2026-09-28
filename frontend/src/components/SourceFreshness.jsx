import React from "react";

// Last successful read per source (backend/pipeline/source_health.py). Shown on
// every list that can be empty, so "no signal today" and "source unreachable
// since X" can never look the same (PREMORTEM.md section 2).

const DOT = {
  ok: "bg-emerald-500",
  failing: "bg-amber-500",
  down: "bg-red-600",
  stale: "bg-red-600",
  never_read: "bg-gray-300",
};

export function FreshnessStrip({ rows }) {
  if (!rows?.length) return null;
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-gray-500">
      {rows.map((r) => (
        <span key={r.source} className="inline-flex items-center gap-1.5" title={r.last_error || r.message}>
          <span className={`h-1.5 w-1.5 rounded-full ${DOT[r.state] ?? "bg-gray-300"}`} />
          <span className="font-medium text-gray-700">{r.source.replace(/^CRA:/, "")}</span>
          <span className={r.state === "ok" ? "" : "font-medium text-red-700"}>{r.message}</span>
        </span>
      ))}
    </div>
  );
}

// What an empty list means. `verdict` comes from source_health.summarise():
// "quiet" only when every feeding source answered.
export function EmptyState({ verdict, quietTitle, quietText }) {
  if (verdict?.verdict === "quiet") {
    return (
      <div className="text-center px-6">
        <p className="text-base font-semibold text-gray-900">{quietTitle}</p>
        <p className="mt-1.5 text-sm text-gray-500">{quietText}</p>
        <p className="mt-2 text-xs text-gray-400">{verdict.text}</p>
      </div>
    );
  }
  if (verdict?.verdict === "degraded") {
    return (
      <div className="text-center px-6">
        <p className="text-base font-semibold text-red-700">Empty because a source is down - not a quiet day</p>
        <p className="mt-1.5 text-sm text-red-600">{verdict.text}</p>
        <p className="mt-2 text-xs text-gray-500">Anything that source would have shown is missing from this list.</p>
      </div>
    );
  }
  return (
    <div className="text-center px-6">
      <p className="text-base font-semibold text-gray-900">Nothing read yet</p>
      <p className="mt-1.5 text-sm text-gray-500">
        {verdict?.text ?? "No source has answered since the server started."} An empty list here proves nothing either way.
      </p>
    </div>
  );
}
