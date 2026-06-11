import React, { useState, useEffect, useRef } from "react";
import { apiUrl } from "../../lib/api.js";

// ── Autocomplete search box ───────────────────────────────────────────────────
function CompanySearchInput({ onSearch, loading }) {
  const [query, setQuery]           = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [sugLoading, setSugLoading]  = useState(false);
  const [open, setOpen]              = useState(false);
  const debounce                     = useRef(null);
  const boxRef                       = useRef(null);

  const isCIN = /^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$/i.test(query.trim());

  // Fetch suggestions with debounce
  useEffect(() => {
    clearTimeout(debounce.current);
    if (query.trim().length < 2) { setSuggestions([]); return; }
    debounce.current = setTimeout(async () => {
      setSugLoading(true);
      try {
        const r = await fetch(apiUrl(`/api/company-suggest?q=${encodeURIComponent(query.trim())}`));
        const d = await r.json();
        setSuggestions(d.suggestions ?? []);
        setOpen(true);
      } catch { setSuggestions([]); }
      finally { setSugLoading(false); }
    }, 300);
  }, [query]);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e) => { if (!boxRef.current?.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleSelect = (name) => {
    setQuery(name);
    setOpen(false);
    setSuggestions([]);
    onSearch(name);
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!query.trim()) return;
    setOpen(false);
    onSearch(query.trim());
  };

  return (
    <form onSubmit={handleSubmit} className="flex items-end gap-3 max-w-2xl">
      <div className="flex-1" ref={boxRef}>
        <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-gray-400">
          Company Name or CIN
        </label>
        <div className="relative">
          {/* Icon */}
          <svg className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400"
            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
          </svg>

          <input
            type="text"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
            onFocus={() => suggestions.length && setOpen(true)}
            placeholder='e.g. "Bajaj Finance", "TATA", "L65910MH1987PLC042961"'
            className="w-full rounded-lg border border-gray-300 bg-white py-2.5 pl-9 pr-16 text-sm
              text-gray-900 placeholder-gray-400 outline-none transition
              focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
          />

          {/* CIN badge or loading spinner */}
          <div className="absolute right-3 top-1/2 -translate-y-1/2 flex items-center gap-1">
            {sugLoading && (
              <svg className="h-3.5 w-3.5 animate-spin text-gray-400" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
            )}
            {isCIN && (
              <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-bold text-blue-600 border border-blue-200">
                CIN
              </span>
            )}
          </div>

          {/* Suggestions dropdown */}
          {open && suggestions.length > 0 && (
            <div className="absolute top-full left-0 right-0 z-50 mt-1 max-h-72 overflow-y-auto
              rounded-xl border border-gray-200 bg-white shadow-xl">
              {suggestions.map((s, i) => (
                <button
                  key={i}
                  type="button"
                  onMouseDown={() => handleSelect(s.name)}
                  className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-blue-50
                    transition border-b border-gray-50 last:border-0"
                >
                  <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center
                    rounded-lg bg-gray-100 text-xs font-bold text-gray-500">
                    {s.name.charAt(0)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold text-gray-900">{s.name}</div>
                    <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                      {s.cin && (
                        <span className="text-[10px] text-gray-400 font-mono">{s.cin}</span>
                      )}
                      {s.bse_code && (
                        <span className="text-[10px] text-gray-400 font-mono">BSE: {s.bse_code}</span>
                      )}
                      {s.sector && (
                        <span className="text-[10px] text-gray-400">{s.sector}</span>
                      )}
                      {s.source === "rbi_registry" && (
                        <span className="rounded bg-emerald-50 px-1 py-px text-[9px] font-bold text-emerald-600 border border-emerald-100">
                          RBI
                        </span>
                      )}
                    </div>
                  </div>
                  <svg className="h-4 w-4 shrink-0 text-gray-300 mt-1" fill="none" viewBox="0 0 24 24"
                    stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7"/>
                  </svg>
                </button>
              ))}
            </div>
          )}
        </div>
        <p className="mt-1 text-[11px] text-gray-400">
          Searches every NSE-listed company (all sectors) + 10,500+ RBI-registered NBFCs &amp; banks · or paste a CIN
        </p>
      </div>

      <button
        type="submit"
        disabled={loading || !query.trim()}
        className="flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold
          text-white shadow-sm transition hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {loading ? (
          <>
            <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
            </svg>
            Searching...
          </>
        ) : (
          <>
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
            </svg>
            Research
          </>
        )}
      </button>
    </form>
  );
}

// ── Rating badge ──────────────────────────────────────────────────────────────
function RatingBadge({ rating }) {
  if (!rating || rating === "—") return <span className="text-gray-300">—</span>;
  const cls =
    rating.startsWith("AAA") ? "text-green-700 bg-green-50 border-green-200" :
    rating.startsWith("AA")  ? "text-emerald-700 bg-emerald-50 border-emerald-200" :
    rating.startsWith("A")   ? "text-blue-700 bg-blue-50 border-blue-200" :
    rating.startsWith("BB")  ? "text-amber-700 bg-amber-50 border-amber-200" :
    "text-red-700 bg-red-50 border-red-200";
  return (
    <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-bold ${cls}`}>
      {rating}
    </span>
  );
}

// ── Agency row ────────────────────────────────────────────────────────────────
function AgencyRow({ agency, unverified }) {
  const [expanded, setExpanded] = useState(false);
  const isInfomerics = agency.key === "INFOMERICS";

  return (
    <>
      <tr className={`border-b border-gray-100 ${isInfomerics ? "bg-blue-50/60" : "hover:bg-gray-50"} transition`}>
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            {isInfomerics && (
              <span className="rounded bg-blue-600 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-white">
                US
              </span>
            )}
            <span className={`text-sm font-semibold ${isInfomerics ? "text-blue-700" : "text-gray-800"}`}>
              {agency.full_name}
            </span>
          </div>
        </td>
        <td className="px-4 py-3">
          {agency.is_rated ? <RatingBadge rating={agency.latest_rating} /> : (
            <span className={`text-xs ${unverified ? "text-amber-500 font-medium" : "text-gray-400"}`}>
              {unverified ? "Unknown — verify" : "Not rated"}
            </span>
          )}
        </td>
        <td className="px-4 py-3 text-center">
          {agency.is_rated ? (
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full
              bg-emerald-100 text-[10px] font-bold text-emerald-700">
              {agency.total_instruments}
            </span>
          ) : (
            <span className="text-xs text-gray-300">0</span>
          )}
        </td>
        <td className="px-4 py-3">
          <div className={`flex items-center gap-1.5`}>
            <span className={`h-2 w-2 rounded-full ${agency.is_rated ? "bg-green-400" : unverified ? "bg-amber-300" : "bg-gray-200"}`} />
            <span className={`text-xs font-medium ${agency.is_rated ? "text-green-600" : unverified ? "text-amber-500" : "text-gray-400"}`}>
              {agency.is_rated ? "Rated" : unverified ? "Unverified" : "Not found"}
            </span>
          </div>
        </td>
        <td className="px-4 py-3">
          <div className="flex items-center gap-3">
            {agency.is_rated && (
              <button
                onClick={() => setExpanded(!expanded)}
                className="text-xs text-blue-500 hover:text-blue-700 font-medium"
              >
                {expanded ? "Hide ▲" : `View ${agency.total_instruments} ▼`}
              </button>
            )}
            <a href={agency.search_url} target="_blank" rel="noreferrer"
              className="text-xs text-gray-400 hover:text-blue-500 transition">
              Search →
            </a>
          </div>
        </td>
      </tr>

      {expanded && agency.instruments.map((inst, i) => (
        <tr key={i} className="border-b border-gray-50 bg-gray-50/50">
          <td className="py-2 pl-12 pr-4 text-xs" colSpan={2}>
            <div className="font-medium text-gray-700">{inst.security_name || "—"}</div>
            {inst.isin && <div className="font-mono text-[10px] text-gray-400">{inst.isin}</div>}
          </td>
          <td className="py-2 px-4 text-xs text-gray-500">{inst.instrument_type || "—"}</td>
          <td className="py-2 px-4 text-xs text-gray-500">{inst.coupon_rate ? `${inst.coupon_rate}%` : "—"}</td>
          <td className="py-2 px-4 text-xs text-gray-500">{inst.maturity_date || inst.issue_date || "—"}</td>
        </tr>
      ))}
    </>
  );
}

// ── ACER Fit Card ─────────────────────────────────────────────────────────────
function FitAnalysisCard({ fit, loading }) {
  if (loading) {
    return (
      <div className="rounded-2xl border border-amber-200 bg-gradient-to-br from-amber-50 to-yellow-50 p-5 animate-pulse">
        <div className="skeleton mb-3 h-5 w-3/4 rounded" />
        <div className="skeleton mb-4 h-16 w-full rounded-xl" />
        <div className="space-y-2">
          {[1,2,3].map(i => <div key={i} className="skeleton h-3 w-full rounded" />)}
        </div>
      </div>
    );
  }
  if (!fit) return null;

  const urgencyColor = {
    High:   "bg-red-100 text-red-700 border-red-200",
    Medium: "bg-amber-100 text-amber-700 border-amber-200",
    Low:    "bg-gray-100 text-gray-600 border-gray-200",
  }[fit.urgency] ?? "bg-gray-100 text-gray-600 border-gray-200";

  const scoreColor =
    fit.fit_score >= 80 ? "text-green-700" :
    fit.fit_score >= 60 ? "text-blue-700" :
    fit.fit_score >= 40 ? "text-amber-700" : "text-gray-500";

  return (
    <div className="rounded-2xl border border-amber-200 bg-gradient-to-br from-amber-50 to-yellow-50 p-5 flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-amber-100">
            <svg className="h-5 w-5 text-amber-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round"
                d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/>
            </svg>
          </div>
          <div>
            <div className="text-[10px] font-black uppercase tracking-widest text-amber-600">ACER Fit Analysis</div>
            <div className="text-[10px] text-gray-500">AI-powered</div>
          </div>
        </div>
        <span className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-bold ${urgencyColor}`}>
          {fit.urgency} Priority
        </span>
      </div>

      {/* Score */}
      <div className="rounded-xl border border-white/80 bg-white/70 px-4 py-3 text-center shadow-sm">
        <div className={`text-4xl font-black ${scoreColor}`}>{fit.fit_score}</div>
        <div className={`mt-0.5 text-sm font-bold ${scoreColor}`}>{fit.fit_label}</div>
        <div className="mt-1 text-[10px] text-gray-400">out of 100</div>
      </div>

      {/* Opportunity */}
      {fit.opportunity_type && (
        <div className="rounded-lg border border-amber-200/80 bg-white/50 px-3 py-2">
          <div className="text-[10px] font-bold uppercase tracking-wider text-amber-600">Opportunity</div>
          <div className="mt-0.5 text-xs font-medium text-gray-800">{fit.opportunity_type}</div>
        </div>
      )}

      {/* Already rated by Infomerics */}
      {fit.already_rated_by_infomerics && (
        <div className="flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2">
          <svg className="h-4 w-4 text-blue-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
          </svg>
          <span className="text-xs font-semibold text-blue-700">Already rated by Infomerics</span>
        </div>
      )}

      {/* Key Insights */}
      {fit.key_insights?.length > 0 && (
        <div>
          <div className="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-gray-500">Key Insights</div>
          <ul className="space-y-1.5">
            {fit.key_insights.map((x, i) => (
              <li key={i} className="flex gap-2 text-xs text-gray-700">
                <span className="mt-0.5 shrink-0 text-green-500">✓</span>
                <span>{x}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Watch Outs */}
      {fit.watch_outs?.length > 0 && (
        <div>
          <div className="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-gray-500">Watch Out</div>
          <ul className="space-y-1.5">
            {fit.watch_outs.map((w, i) => (
              <li key={i} className="flex gap-2 text-xs text-gray-600">
                <span className="mt-0.5 shrink-0 text-amber-500">⚠</span>
                <span>{w}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Best pitch */}
      {fit.best_instrument_pitch && (
        <div className="flex items-center justify-between rounded-lg border border-gray-200 bg-white/80 px-3 py-2">
          <span className="text-xs text-gray-500">Best instrument to pitch</span>
          <span className="rounded-full bg-blue-100 px-2.5 py-0.5 text-xs font-bold text-blue-700">
            {fit.best_instrument_pitch}
          </span>
        </div>
      )}

      {/* Action */}
      {fit.recommended_action && (
        <div className="rounded-xl bg-blue-600 px-4 py-3 text-center">
          <div className="text-[10px] font-bold uppercase tracking-wider text-blue-200">Recommended Action</div>
          <div className="mt-1 text-sm font-semibold leading-snug text-white">
            {fit.recommended_action}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Company info strip ────────────────────────────────────────────────────────
function CompanyInfo({ company }) {
  if (!company?.name) return null;
  return (
    <div className="mb-5 rounded-xl border border-gray-200 bg-white p-4">
      <h2 className="text-base font-bold text-gray-900">{company.name}</h2>
      <div className="mt-2 flex flex-wrap gap-4 text-xs">
        {[
          { label: "Type",         value: [company.entity_type, company.sub_type].filter(Boolean).join(" · ") },
          { label: "CIN",          value: company.cin },
          { label: "Incorporated", value: company.incorporation_date },
          { label: "Email",        value: company.email },
          { label: "Location",     value: company.address?.split(",").slice(0, 2).join(",") },
          { label: "Source",       value: company.data_source },
        ].filter(x => x.value).map(({ label, value }) => (
          <div key={label}>
            <span className="text-gray-400">{label}: </span>
            <span className="font-semibold text-gray-700">{value}</span>
          </div>
        ))}
      </div>

      {company.directors?.length > 0 && (
        <div className="mt-3 border-t border-gray-100 pt-3">
          <div className="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">
            Management &amp; Board
          </div>
          <div className="flex flex-wrap gap-1.5">
            {company.directors.map((d, i) => (
              <a
                key={i}
                href={`https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(`${d.name} ${company.name}`)}`}
                target="_blank" rel="noreferrer"
                className="group inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-gray-50
                  px-2.5 py-1 text-xs text-gray-700 transition hover:border-blue-300 hover:bg-blue-50"
                title={`${d.designation || "Director"} — search on LinkedIn`}
              >
                <span className="font-medium">{d.name}</span>
                {d.designation && (
                  <span className="text-[10px] text-gray-400 group-hover:text-blue-500">
                    {d.designation}
                  </span>
                )}
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function CompanyResearchPage() {
  const [loading, setLoading] = useState(false);
  const [result,  setResult]  = useState(null);
  const [error,   setError]   = useState("");

  const handleSearch = async (query) => {
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await fetch(apiUrl("/api/company-credit"), {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ query }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? `HTTP ${res.status}`);
      setResult(await res.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const agencies        = result?.credit_data?.agencies        ?? [];
  const fit             = result?.fit_analysis                 ?? null;
  const company         = result?.company                      ?? null;
  const totalInstruments = result?.credit_data?.total_instruments ?? 0;
  const ratedByCount    = result?.credit_data?.rated_by_count   ?? 0;
  const dataStatus      = result?.credit_data?.data_status      ?? "ok";
  const unverified      = dataStatus === "unverified";

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-gray-50">

      {/* Search bar */}
      <div className="shrink-0 border-b border-gray-200 bg-white px-6 py-4">
        <CompanySearchInput onSearch={handleSearch} loading={loading} />
        {error && (
          <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}
      </div>

      {/* Content */}
      <div className="flex flex-1 overflow-hidden">

        {/* Left: credit history */}
        <div className="flex flex-1 flex-col overflow-y-auto p-6">

          {!result && !loading && (
            <div className="flex flex-1 flex-col items-center justify-center py-20 text-center">
              <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-blue-50">
                <svg className="h-8 w-8 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round"
                    d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
                </svg>
              </div>
              <p className="text-base font-semibold text-gray-900">Company Credit Research</p>
              <p className="mt-2 max-w-sm text-sm text-gray-500">
                Search any Indian company to see its full credit history across all 7 SEBI-registered agencies
                and get an AI-powered ACER fit analysis.
              </p>
              <div className="mt-5 flex flex-wrap justify-center gap-2 text-xs text-gray-500">
                {["CRISIL","ICRA","CARE Ratings","India Ratings","Acuité","Brickwork","Infomerics"].map(a => (
                  <span key={a} className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 font-medium">{a}</span>
                ))}
              </div>
            </div>
          )}

          {result && (
            <>
              <CompanyInfo company={company} />

              {unverified && (
                <div className="mb-4 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
                  <span className="mt-0.5 text-amber-500">⚠</span>
                  <div className="text-xs text-amber-800">
                    <span className="font-bold">Rating history could not be verified right now</span> — NSE/BSE
                    disclosure sources are unreachable. The statuses below mean
                    <span className="font-semibold"> unknown</span>, not unrated. Use the per-agency Search
                    links to verify manually.
                  </div>
                </div>
              )}

              {/* Stats */}
              <div className="mb-5 grid grid-cols-3 gap-3">
                {[
                  { label: "Rating Actions & Instruments", value: unverified ? "—" : totalInstruments, color: "text-blue-600 bg-blue-50" },
                  { label: "Agencies Rating",   value: unverified ? "?" : `${ratedByCount} / 7`, color: "text-emerald-600 bg-emerald-50" },
                  { label: unverified ? "Unverified" : "Not Rated By", value: unverified ? "7" : 7 - ratedByCount, color: "text-amber-600 bg-amber-50" },
                ].map(({ label, value, color }) => (
                  <div key={label} className="rounded-xl border border-gray-200 bg-white p-4 text-center">
                    <div className={`text-2xl font-black ${color}`}>{value}</div>
                    <div className="mt-0.5 text-[11px] text-gray-500">{label}</div>
                  </div>
                ))}
              </div>

              {/* Agency table */}
              <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
                <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
                  <h3 className="text-sm font-bold text-gray-900">
                    Credit Rating History — All 7 Agencies
                  </h3>
                  <span className="text-xs text-gray-400">Source: NSE corporate disclosures + BSE debt data</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-gray-100 bg-gray-50">
                        {["Agency","Latest Rating","Instruments","Status","Actions"].map(h => (
                          <th key={h} className="px-4 py-2.5 text-left text-xs font-semibold text-gray-500">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {agencies.map(agency => (
                        <AgencyRow key={agency.key} agency={agency} unverified={unverified} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {(result?.credit_data?.rating_actions?.length ?? 0) > 0 && (
                <div className="mt-5 overflow-hidden rounded-xl border border-gray-200 bg-white">
                  <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
                    <h3 className="text-sm font-bold text-gray-900">
                      Rating Disclosure Timeline ({result.credit_data.rating_actions.length})
                    </h3>
                    <span className="text-xs text-gray-400">NSE corporate filings — click to open the actual document</span>
                  </div>
                  <div className="max-h-80 overflow-y-auto divide-y divide-gray-50">
                    {result.credit_data.rating_actions.map((a, i) => (
                      <div key={i} className="flex items-start gap-3 px-4 py-2.5 hover:bg-gray-50">
                        <span className="mt-0.5 w-20 shrink-0 font-mono text-[11px] text-gray-400">{a.date}</span>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className={`text-xs font-semibold ${a.agency?.startsWith("Disclosed") ? "text-gray-500" : "text-gray-800"}`}>
                              {a.agency}
                            </span>
                            {a.rating && a.rating !== "See filing" && (
                              <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-700">
                                {a.rating}
                              </span>
                            )}
                            <span className={`text-[10px] font-medium ${
                              /upgrad/i.test(a.action) ? "text-green-600" :
                              /downgrad|withdraw/i.test(a.action) ? "text-red-500" : "text-gray-400"}`}>
                              {a.action}
                            </span>
                          </div>
                          {a.detail && (
                            <div className="mt-0.5 truncate text-[11px] text-gray-400" title={a.detail}>{a.detail}</div>
                          )}
                        </div>
                        {a.attachment && (
                          <a href={a.attachment} target="_blank" rel="noreferrer"
                            className="shrink-0 text-[11px] font-medium text-blue-600 hover:underline">
                            Filing ↗
                          </a>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {totalInstruments === 0 && !unverified && (
                <p className="mt-4 text-center text-sm text-gray-400">
                  No exchange-disclosed rating actions or listed instruments matched this exact name —
                  company may be unrated, privately rated, or disclosed under a different legal spelling.
                </p>
              )}
            </>
          )}
        </div>

        {/* Right: Fit Analysis */}
        <div className="w-80 shrink-0 overflow-y-auto border-l border-gray-200 bg-gray-50 p-5 xl:w-96">
          {(result || loading) ? (
            <FitAnalysisCard fit={fit} loading={loading} />
          ) : (
            <div className="flex h-full flex-col items-center justify-center py-12 text-center">
              <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-amber-50">
                <svg className="h-6 w-6 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round"
                    d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/>
                </svg>
              </div>
              <p className="text-sm font-semibold text-gray-700">ACER Fit Analysis</p>
              <p className="mt-1.5 text-xs leading-relaxed text-gray-400">
                Search a company to get an AI assessment of whether it's a good fit for Infomerics to pursue.
              </p>
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
