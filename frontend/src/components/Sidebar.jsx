import React from "react";
import LeadScore from "./LeadScore.jsx";

const ENTITY_BADGE = {
  "Bank":             "text-blue-700 bg-blue-50 border border-blue-200",
  "NBFC":             "text-violet-700 bg-violet-50 border border-violet-200",
  "Corporate":        "text-emerald-700 bg-emerald-50 border border-emerald-200",
  "Financial Entity": "text-gray-600 bg-gray-100 border border-gray-200",
};

function SkeletonCard() {
  return (
    <div className="border-b border-gray-100 p-4">
      <div className="skeleton mb-2 h-4 w-3/4 rounded" />
      <div className="skeleton mb-2 h-3 w-1/3 rounded" />
      <div className="skeleton h-3 w-1/2 rounded" />
    </div>
  );
}

export default function Sidebar({ companies, loading, selectedId, onSelectCompany, searchId, city, industry }) {
  const handleExport = () => {
    if (!searchId) return;
    window.open(`/api/export/${searchId}`, "_blank");
  };

  return (
    <div className="flex h-full flex-col bg-white">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
        <div>
          <div className="text-sm font-semibold text-gray-900">
            {loading ? "Scanning..." : companies.length ? `${companies.length} Leads Found` : "Leads"}
          </div>
          {(city || industry) && (
            <div className="mt-0.5 text-xs text-gray-500 truncate max-w-[160px]">{industry || city}</div>
          )}
        </div>
        {searchId && !loading && (
          <button
            onClick={handleExport}
            className="flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-2.5 py-1.5
              text-xs font-medium text-gray-600 transition hover:border-blue-400 hover:text-blue-600"
          >
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round"
                d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            Export CSV
          </button>
        )}
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          Array.from({ length: 8 }).map((_, i) => <SkeletonCard key={i} />)
        ) : companies.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 px-4 text-center text-gray-400">
            <svg className="mb-3 h-10 w-10 opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <p className="text-sm text-gray-500">No leads yet</p>
            <p className="mt-1 text-xs text-gray-400">Use the search above to find leads</p>
          </div>
        ) : (
          companies.map((company, idx) => (
            <button
              key={company.id}
              onClick={() => onSelectCompany(company.id)}
              className={`w-full border-b border-gray-100 p-4 text-left transition
                hover:bg-blue-50/50
                ${selectedId === company.id
                  ? "border-l-2 border-l-blue-500 bg-blue-50/60"
                  : "border-l-2 border-l-transparent"
                }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-gray-300">{idx + 1}</span>
                    <span className="truncate text-sm font-semibold text-gray-900">{company.name}</span>
                  </div>
                  <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
                    {company.entity_type && (
                      <span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold
                        ${ENTITY_BADGE[company.entity_type] ?? ENTITY_BADGE["Financial Entity"]}`}>
                        {company.entity_type}
                      </span>
                    )}
                    <LeadScore label={company.score_label} score={company.score} />
                  </div>
                  <div className="mt-1 flex items-center gap-2">
                    <div className="truncate text-xs text-gray-400">
                      {company.address.split(",").slice(0, 2).join(",")}
                    </div>
                    {company.past_instruments?.length > 0 && (
                      <span className="shrink-0 rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 border border-amber-100">
                        {company.past_instruments.length} instruments
                      </span>
                    )}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <span className={`text-lg font-bold
                    ${company.score >= 80 ? "text-red-500" :
                      company.score >= 60 ? "text-orange-500" :
                      company.score >= 40 ? "text-amber-500" : "text-gray-300"}`}>
                    {company.score || "—"}
                  </span>
                </div>
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
