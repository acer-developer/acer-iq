/**
 * Per-source health strip for a completed request.
 *
 * The whole point: an empty result must never look like a fact. If BSE errored
 * or the AI key is missing, the sales team sees it here instead of trusting a
 * blank instruments table.
 */
export default function SourceHealth({ sources, className = "" }) {
  const degraded = (sources ?? []).filter((s) => !s.ok);
  if (!degraded.length) return null;

  return (
    <div
      className={`rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 ${className}`}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-amber-900">
        <span className="font-semibold">Incomplete data —</span>
        {degraded.map((s) => (
          <span key={s.name} className="inline-flex items-center gap-1">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
            <span className="font-medium">{s.name}</span>
            {s.detail && <span className="text-amber-700">({s.detail})</span>}
          </span>
        ))}
        <span className="text-amber-700">
          Anything missing below may be a source failure, not a fact.
        </span>
      </div>
    </div>
  );
}
