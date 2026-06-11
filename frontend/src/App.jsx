import React, { useState } from "react";
import SearchBar from "./components/SearchBar.jsx";
import MapView from "./components/MapView.jsx";
import Sidebar from "./components/Sidebar.jsx";
import CompanyCard from "./components/CompanyCard.jsx";
import CompanyResearchPage from "./components/company/CompanyResearchPage.jsx";
import { apiUrl } from "./lib/api.js";

const MAPS_API_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY ?? "";

function TabBar({ active, onChange }) {
  return (
    <div className="flex border-b border-gray-200 bg-white px-5">
      {[
        { id: "leads", icon: (
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        ), label: "Find Leads" },
        { id: "research", icon: (
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
          </svg>
        ), label: "Company Research" },
      ].map((tab) => (
        <button
          key={tab.id}
          onClick={() => onChange(tab.id)}
          className={`flex items-center gap-2 border-b-2 px-4 py-3 text-sm font-medium transition
            ${active === tab.id
              ? "border-blue-600 text-blue-600"
              : "border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700"
            }`}
        >
          {tab.icon}
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState("leads");

  // Find Leads state
  const [companies, setCompanies] = useState([]);
  const [cityLat, setCityLat] = useState(20.5937);
  const [cityLng, setCityLng] = useState(78.9629);
  const [selectedId, setSelectedId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [searchId, setSearchId] = useState(null);
  const [searchLocation, setSearchLocation] = useState("");
  const [searchEntity, setSearchEntity] = useState("");
  const [searchInstrument, setSearchInstrument] = useState("");
  const [detailOpen, setDetailOpen] = useState(false);

  const selectedCompany = companies.find((c) => c.id === selectedId) ?? null;
  const activeOfficeLocations = selectedCompany?.office_locations ?? [];
  const officesFetched = React.useRef(new Set());

  // Branch offices are fetched on demand when a lead is selected —
  // not in bulk during search (saves 60 Google Places calls per search)
  const handleSelectCompany = (id) => {
    setSelectedId(id);
    setDetailOpen(true);
    const comp = companies.find((c) => c.id === id);
    if (!comp || officesFetched.current.has(id)) return;
    officesFetched.current.add(id);
    if (!comp.office_locations?.length) {
      fetch(apiUrl(`/api/offices/${encodeURIComponent(comp.name)}?lat=${comp.lat}&lng=${comp.lng}`))
        .then((r) => (r.ok ? r.json() : { offices: [] }))
        .then((d) => {
          if (d.offices?.length) {
            setCompanies((prev) =>
              prev.map((c) => (c.id === id ? { ...c, office_locations: d.offices } : c))
            );
          }
        })
        .catch(() => {});
    }
    // Board of directors loads on demand too (BSE CorpInfo / Zauba)
    if (!comp.directors?.length) {
      fetch(apiUrl(`/api/directors/${encodeURIComponent(comp.name)}`))
        .then((r) => (r.ok ? r.json() : { directors: [] }))
        .then((d) => {
          if (d.directors?.length || d.cin) {
            setCompanies((prev) =>
              prev.map((c) => (c.id === id
                ? {
                    ...c,
                    directors: d.directors?.length ? d.directors : c.directors,
                    cin: c.cin || d.cin || "",
                  }
                : c))
            );
          }
        })
        .catch(() => {});
    }
  };

  const handleSearch = async (location, entityType, instrumentType, size = "All") => {
    setLoading(true);
    setError("");
    setSelectedId(null);
    setDetailOpen(false);
    setSearchLocation(location);
    setSearchEntity(entityType);
    setSearchInstrument(instrumentType);

    try {
      const res = await fetch(apiUrl("/api/search"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          city: location,
          entity_type: entityType,
          instrument_type: instrumentType,
          size,
          industry: `${entityType} — ${instrumentType}`,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail ?? `HTTP ${res.status}`);
      }

      const data = await res.json();
      setCompanies(data.companies ?? []);
      setCityLat(data.city_lat);
      setCityLng(data.city_lng);
      setSearchId(data.search_id);
    } catch (e) {
      setError(e.message ?? "Search failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const searchDesc = [searchLocation, searchEntity, searchInstrument].filter(Boolean).join(" · ");

  return (
    <div className="flex h-screen flex-col bg-gray-50 overflow-hidden">

      {/* Brand bar */}
      <header className="z-20 shrink-0 border-b border-gray-100 bg-white shadow-sm">
        <div className="flex items-center gap-3 px-5 py-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-blue-600">
            <svg className="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round"
                d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
            </svg>
          </div>
          <div>
            <span className="text-sm font-bold text-gray-900">LeadRadar</span>
            <span className="ml-2 text-xs text-gray-400">Credit Rating Intelligence · India</span>
          </div>
          {companies.length > 0 && !loading && activeTab === "leads" && (
            <div className="ml-auto flex items-center gap-2 text-xs text-gray-500">
              <span className="rounded-full bg-blue-50 px-2.5 py-1 font-medium text-blue-600">
                {companies.length} leads found
              </span>
              {searchDesc && <span className="text-gray-400">{searchDesc}</span>}
            </div>
          )}
        </div>
      </header>

      {/* Tabs */}
      <TabBar active={activeTab} onChange={(t) => { setActiveTab(t); setError(""); }} />

      {/* ── Find Leads Tab ─────────────────────────────────────────────── */}
      {activeTab === "leads" && (
        <>
          {/* Search form */}
          <div className="shrink-0 border-b border-gray-200 bg-white px-5 py-3">
            <SearchBar onSearch={handleSearch} loading={loading} />
          </div>

          {error && (
            <div className="shrink-0 border-b border-red-200 bg-red-50 px-5 py-2 text-sm text-red-700">
              <span className="font-semibold">Error:</span> {error}
              <span className="ml-2 text-xs text-red-500">
                (Make sure the backend is running: <code className="font-mono">uvicorn backend.main:app --reload --port 8000</code>)
              </span>
            </div>
          )}

          {loading && (
            <div className="shrink-0 flex items-center gap-2 border-b border-blue-100 bg-blue-50 px-5 py-2 text-sm text-blue-700">
              <svg className="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <span>
                Scanning <span className="font-semibold">{searchLocation}</span>
                {searchEntity !== "All" && <> for <span className="font-semibold">{searchEntity}</span></>}
                {searchInstrument !== "All" && <> · <span className="font-semibold">{searchInstrument}</span></>}
                {" "}— discovering companies, fetching BSE history, scoring with AI...
              </span>
            </div>
          )}

          <div className="flex flex-1 overflow-hidden">
            <aside className="w-72 shrink-0 overflow-hidden border-r border-gray-200 bg-white md:w-80">
              <Sidebar
                companies={companies}
                loading={loading}
                selectedId={selectedId}
                onSelectCompany={handleSelectCompany}
                searchId={searchId}
                city={searchLocation}
                industry={searchDesc}
              />
            </aside>

            <main className="relative flex-1 overflow-hidden bg-gray-100">
              <MapView
                companies={companies}
                cityLat={cityLat}
                cityLng={cityLng}
                selectedId={selectedId}
                onSelectCompany={handleSelectCompany}
                mapsApiKey={MAPS_API_KEY}
                officeLocations={activeOfficeLocations}
              />

              {companies.length > 0 && !loading && (
                <div className="pointer-events-none absolute bottom-4 left-4 rounded-xl border border-gray-200
                  bg-white/95 px-3 py-2.5 shadow-lg backdrop-blur-sm">
                  <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400">Legend</p>
                  <div className="space-y-1.5">
                    {[
                      { color: "bg-blue-500",    label: "Bank" },
                      { color: "bg-violet-500",  label: "NBFC" },
                      { color: "bg-emerald-500", label: "Corporate" },
                    ].map(({ color, label }) => (
                      <div key={label} className="flex items-center gap-2">
                        <span className={`h-2.5 w-2.5 rounded-full ${color}`} />
                        <span className="text-xs text-gray-600">{label}</span>
                      </div>
                    ))}
                    {activeOfficeLocations.length > 0 && (
                      <div className="flex items-center gap-2 border-t border-gray-100 pt-1.5 mt-0.5">
                        <span className="h-2.5 w-2.5 rounded-full bg-cyan-400" />
                        <span className="text-xs text-gray-600">Branch office</span>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {!loading && companies.length === 0 && !error && (
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                  <div className="rounded-2xl border border-gray-200 bg-white px-8 py-7 text-center shadow-xl">
                    <div className="mb-4 flex justify-center">
                      <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50">
                        <svg className="h-7 w-7 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                          <path strokeLinecap="round" strokeLinejoin="round"
                            d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                        </svg>
                      </div>
                    </div>
                    <p className="text-base font-semibold text-gray-900">Find Credit Rating Leads</p>
                    <p className="mt-1.5 text-sm text-gray-500">
                      Select a state, city and instrument type<br />to discover qualified leads across India.
                    </p>
                    <div className="mt-4 space-y-2 text-xs">
                      <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-gray-500">
                        Maharashtra → Mumbai · Banks · Bonds
                      </div>
                      <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-gray-500">
                        Gujarat → Ahmedabad · NBFCs · NCD
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </main>

            {detailOpen && selectedCompany && (
              <aside className="w-80 shrink-0 overflow-hidden border-l border-gray-200 bg-white xl:w-96">
                <CompanyCard
                  company={selectedCompany}
                  onClose={() => { setDetailOpen(false); setSelectedId(null); }}
                />
              </aside>
            )}
          </div>
        </>
      )}

      {/* ── Company Research Tab ───────────────────────────────────────── */}
      {activeTab === "research" && (
        <div className="flex flex-1 overflow-hidden">
          <CompanyResearchPage />
        </div>
      )}

    </div>
  );
}
