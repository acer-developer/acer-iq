import React, { useState } from "react";
import LeadScore from "./LeadScore.jsx";

const ENTITY_COLORS = {
  "Bank":             "text-blue-700 bg-blue-50 border-blue-200",
  "NBFC":             "text-violet-700 bg-violet-50 border-violet-200",
  "Corporate":        "text-emerald-700 bg-emerald-50 border-emerald-200",
  "Financial Entity": "text-gray-600 bg-gray-100 border-gray-200",
};

const INSTRUMENT_COLORS = {
  "NCD":       "text-orange-700 bg-orange-50 border border-orange-200",
  "Bond":      "text-blue-700 bg-blue-50 border border-blue-200",
  "IPO":       "text-green-700 bg-green-50 border border-green-200",
  "Debenture": "text-amber-700 bg-amber-50 border border-amber-200",
};

function Section({ title, icon, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-gray-100 pt-4">
      <button
        onClick={() => setOpen(!open)}
        className="mb-3 flex w-full items-center justify-between gap-2 text-left"
      >
        <h4 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-gray-400">
          {icon}
          {title}
        </h4>
        <svg
          className={`h-3.5 w-3.5 text-gray-300 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && children}
    </div>
  );
}

function BulletList({ items, itemClass = "text-gray-700" }) {
  if (!items?.length) return <p className="text-xs text-gray-400">No data available</p>;
  return (
    <ul className="space-y-2">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2 text-sm">
          <span className="mt-0.5 text-blue-500 shrink-0">•</span>
          <span className={itemClass}>{item}</span>
        </li>
      ))}
    </ul>
  );
}

function InfoRow({ label, value, href }) {
  return (
    <div className="flex gap-3 text-sm">
      <span className="w-28 shrink-0 text-gray-400">{label}</span>
      {href ? (
        <a href={href} target="_blank" rel="noreferrer"
          className="truncate text-blue-600 hover:text-blue-700 hover:underline">
          {value || "—"}
        </a>
      ) : (
        <span className="text-gray-800">{value || "—"}</span>
      )}
    </div>
  );
}

function InstrumentTypeBadge({ type }) {
  const key = Object.keys(INSTRUMENT_COLORS).find(k => type?.toUpperCase().includes(k)) || "Bond";
  const cls = INSTRUMENT_COLORS[key] || "text-gray-600 bg-gray-100";
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold ${cls}`}>
      {type || "Debt"}
    </span>
  );
}

function PastInstrumentsTable({ instruments }) {
  if (!instruments?.length) {
    return (
      <div className="rounded-lg border border-gray-100 bg-gray-50 p-4 text-center">
        <svg className="mx-auto mb-2 h-6 w-6 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10" />
        </svg>
        <p className="text-xs font-medium text-gray-500">No BSE-listed instruments found</p>
        <p className="mt-0.5 text-[10px] text-gray-400">
          Company may have unlisted debt or no past issuances
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50">
            <th className="px-2.5 py-2 text-left font-semibold text-gray-500">Instrument</th>
            <th className="px-2.5 py-2 text-left font-semibold text-gray-500">Type</th>
            <th className="px-2.5 py-2 text-left font-semibold text-gray-500">Coupon</th>
            <th className="px-2.5 py-2 text-left font-semibold text-gray-500">Maturity</th>
            <th className="px-2.5 py-2 text-left font-semibold text-gray-500">Rating</th>
            <th className="px-2.5 py-2 text-left font-semibold text-gray-500">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {instruments.map((inst, i) => (
            <tr key={i} className="hover:bg-gray-50">
              <td className="px-2.5 py-2 max-w-[120px]">
                <div className="truncate font-medium text-gray-800" title={inst.security_name}>
                  {inst.security_name || "—"}
                </div>
                {inst.isin && (
                  <div className="font-mono text-[10px] text-gray-400">{inst.isin}</div>
                )}
              </td>
              <td className="px-2.5 py-2">
                <InstrumentTypeBadge type={inst.instrument_type} />
              </td>
              <td className="px-2.5 py-2 text-gray-700">
                {inst.coupon_rate ? `${inst.coupon_rate}%` : "—"}
              </td>
              <td className="px-2.5 py-2 text-gray-500">
                {inst.maturity_date || "—"}
              </td>
              <td className="px-2.5 py-2">
                {inst.credit_rating ? (
                  <span className="font-bold text-amber-600">{inst.credit_rating}</span>
                ) : (
                  <span className="text-gray-300">—</span>
                )}
                {inst.rating_agency && (
                  <div className="text-[10px] text-gray-400">{inst.rating_agency}</div>
                )}
              </td>
              <td className="px-2.5 py-2">
                <span className={`text-[10px] font-medium ${
                  inst.status?.toLowerCase().includes("listed") ? "text-green-600" :
                  inst.status?.toLowerCase().includes("redeem") ? "text-gray-400" :
                  "text-gray-500"
                }`}>
                  {inst.status || "—"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OfficeLocationsList({ offices }) {
  if (!offices?.length) {
    return <p className="text-xs text-gray-400">No additional offices found</p>;
  }

  const hq = offices.filter(o => o.location_type === "HQ" || o.location_type === "Head Office");
  const branches = offices.filter(o => o.location_type !== "HQ" && o.location_type !== "Head Office");

  return (
    <div className="space-y-2">
      {hq.map((o, i) => (
        <div key={i} className="flex items-start gap-2 rounded-lg border border-orange-200 bg-orange-50 px-3 py-2">
          <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-orange-400" />
          <div className="min-w-0">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-orange-600">{o.location_type}</div>
            <div className="truncate text-xs font-medium text-gray-800">{o.name}</div>
            <div className="truncate text-[10px] text-gray-500">{o.address}</div>
          </div>
        </div>
      ))}
      {branches.slice(0, 8).map((o, i) => (
        <div key={i} className="flex items-start gap-2 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
          <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-cyan-400" />
          <div className="min-w-0">
            <div className="truncate text-xs font-medium text-gray-700">{o.name}</div>
            <div className="truncate text-[10px] text-gray-400">
              {o.address.split(",").slice(0, 3).join(",")}
            </div>
          </div>
        </div>
      ))}
      {branches.length > 8 && (
        <p className="text-center text-xs text-gray-400">+{branches.length - 8} more branches shown on map</p>
      )}
    </div>
  );
}

export default function CompanyCard({ company, onClose }) {
  if (!company) return null;

  const entityCls = ENTITY_COLORS[company.entity_type] ?? ENTITY_COLORS["Financial Entity"];

  return (
    <div className="flex h-full flex-col bg-white">
      {/* Header */}
      <div className="border-b border-gray-200 p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-bold leading-tight text-gray-900">{company.name}</h2>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {company.entity_type && (
                <span className={`inline-flex rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${entityCls}`}>
                  {company.entity_type}
                </span>
              )}
              <LeadScore label={company.score_label} score={company.score} size="lg" />
            </div>
          </div>
          <button
            onClick={onClose}
            className="shrink-0 rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600 transition"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="mt-3 flex flex-wrap gap-3 text-xs text-gray-500">
          {company.website && (
            <a href={company.website.startsWith("http") ? company.website : `https://${company.website}`}
              target="_blank" rel="noreferrer"
              className="flex items-center gap-1 hover:text-blue-600 transition">
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round"
                  d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101" />
                <path strokeLinecap="round" strokeLinejoin="round"
                  d="M10.172 13.828a4 4 0 015.656 0l4-4a4 4 0 01-5.656-5.656l-1.102 1.101" />
              </svg>
              {company.website.replace(/^https?:\/\//, "").split("/")[0]}
            </a>
          )}
          {company.phone && (
            <span className="flex items-center gap-1">
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round"
                  d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z" />
              </svg>
              {company.phone}
            </span>
          )}
          {company.office_locations?.length > 0 && (
            <span className="flex items-center gap-1 text-cyan-600">
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round"
                  d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
              </svg>
              {company.office_locations.length} locations on map
            </span>
          )}
        </div>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-white">

        <Section title="Company Info" icon={
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
          </svg>
        }>
          <div className="space-y-2">
            <InfoRow label="Address" value={company.address} />
            <InfoRow label="CIN" value={company.cin || "Not found"} />
            <InfoRow label="Incorporated" value={company.incorporation_date || "—"} />
          </div>
        </Section>

        <Section
          title={`Past Instruments${company.past_instruments?.length ? ` (${company.past_instruments.length})` : ""}`}
          icon={
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round"
                d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
          }
          defaultOpen={true}
        >
          <PastInstrumentsTable instruments={company.past_instruments} />
          {company.past_instruments?.length > 0 && (
            <div className="mt-2 text-right">
              <a href="https://www.bseindia.com/markets/Debt/DebtHome.aspx"
                target="_blank" rel="noreferrer"
                className="text-[10px] text-blue-500 hover:underline">
                View full history on BSE →
              </a>
            </div>
          )}
        </Section>

        <Section
          title={`Office Locations${company.office_locations?.length ? ` (${company.office_locations.length})` : ""}`}
          icon={
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round"
                d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          }
          defaultOpen={false}
        >
          <OfficeLocationsList offices={company.office_locations} />
        </Section>

        <Section title="Why Quality Lead" icon={
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z" />
          </svg>
        }>
          <BulletList items={company.why_quality_lead} itemClass="text-green-700" />
        </Section>

        <Section title="Pain Points" icon={
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
        }>
          <BulletList items={company.pain_points} itemClass="text-orange-700" />
        </Section>

        {company.recommended_approach && (
          <Section title="Recommended Approach" icon={
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          }>
            <p className="rounded-lg border border-blue-100 bg-blue-50 p-3 text-sm text-blue-800">
              {company.recommended_approach}
            </p>
          </Section>
        )}

        <Section title="Board of Directors" defaultOpen={false} icon={
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
        }>
          {company.directors?.length ? (
            <div className="overflow-x-auto rounded-lg border border-gray-100">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50">
                    <th className="px-2.5 py-2 text-left font-semibold text-gray-500">Name</th>
                    <th className="px-2.5 py-2 text-left font-semibold text-gray-500">Designation</th>
                    <th className="px-2.5 py-2 text-left font-semibold text-gray-500">LinkedIn</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {company.directors.map((d, i) => (
                    <tr key={i} className="hover:bg-gray-50">
                      <td className="px-2.5 py-2 font-medium text-gray-800">{d.name}</td>
                      <td className="px-2.5 py-2 text-gray-500">{d.designation || "Director"}</td>
                      <td className="px-2.5 py-2">
                        <a href={`https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(d.name + " " + company.name)}`}
                          target="_blank" rel="noreferrer"
                          className="text-blue-600 hover:text-blue-700">
                          Search →
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-xs text-gray-400">
              No directors found.{" "}
              <a href={`https://www.zaubacorp.com/company-search?search=${encodeURIComponent(company.name)}`}
                target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
                Search Zauba Corp →
              </a>
            </p>
          )}
        </Section>

        <Section title="Contact Details" defaultOpen={false} icon={
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        }>
          {company.contacts?.length ? (
            <div className="space-y-2">
              {company.contacts.map((c, i) => (
                <div key={i} className="rounded-lg border border-gray-100 bg-gray-50 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="text-sm font-semibold text-gray-800">{c.name || "Unknown"}</div>
                      {c.position && <div className="text-xs text-gray-500">{c.position}</div>}
                    </div>
                    {c.linkedin_url && (
                      <a href={c.linkedin_url} target="_blank" rel="noreferrer"
                        className="shrink-0 text-xs text-blue-600 hover:underline">
                        LinkedIn
                      </a>
                    )}
                  </div>
                  {c.email && (
                    <a href={`mailto:${c.email}`}
                      className="mt-1.5 flex items-center gap-1 text-xs text-blue-600 hover:underline">
                      {c.email}
                    </a>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-gray-400">
              No contacts found.{" "}
              <a href={`https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(company.name)}`}
                target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
                Search LinkedIn →
              </a>
            </p>
          )}
        </Section>

      </div>
    </div>
  );
}
