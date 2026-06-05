import React, { useState } from "react";
import { INDIA_STATES_CITIES, STATE_LIST } from "../data/indiaData.js";

const ENTITY_TYPES = [
  { value: "All",        label: "All Entities" },
  { value: "Banks",      label: "Banks" },
  { value: "NBFCs",      label: "NBFCs" },
  { value: "Corporates", label: "Corporates" },
];

const INSTRUMENTS = [
  { value: "All",  label: "All Instruments" },
  { value: "NCD",  label: "NCD" },
  { value: "Bond", label: "Bonds" },
  { value: "IPO",  label: "IPO" },
  { value: "Debt", label: "Debt Placement" },
];

function FieldLabel({ children }) {
  return (
    <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-gray-400">
      {children}
    </span>
  );
}

function SelectField({ value, onChange, options, placeholder, disabled, icon }) {
  return (
    <div className="relative">
      {icon && (
        <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">
          {icon}
        </span>
      )}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className={`w-full appearance-none rounded-lg border px-3 py-2.5 pr-8 text-sm outline-none transition
          ${icon ? "pl-9" : "pl-3"}
          ${disabled
            ? "cursor-not-allowed border-gray-200 bg-gray-50 text-gray-400"
            : "cursor-pointer border-gray-300 bg-white text-gray-900 hover:border-blue-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
          }`}
      >
        {placeholder && <option value="">{placeholder}</option>}
        {options.map((o) => (
          <option key={typeof o === "string" ? o : o.value} value={typeof o === "string" ? o : o.value}>
            {typeof o === "string" ? o : o.label}
          </option>
        ))}
      </select>
      <svg
        className={`pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5
          ${disabled ? "text-gray-300" : "text-gray-400"}`}
        fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
      </svg>
    </div>
  );
}

export default function SearchBar({ onSearch, loading }) {
  const [state, setState] = useState("");
  const [city, setCity] = useState("");
  const [pincode, setPincode] = useState("");
  const [usePin, setUsePin] = useState(false);
  const [entityType, setEntityType] = useState("All");
  const [instrumentType, setInstrumentType] = useState("All");

  const cities = state ? INDIA_STATES_CITIES[state] ?? [] : [];

  const handleStateChange = (s) => {
    setState(s);
    setCity("");
  };

  const toggleMode = () => {
    setUsePin(!usePin);
    setCity("");
    setPincode("");
  };

  const isValid = state && (usePin ? /^\d{6}$/.test(pincode) : city);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!isValid) return;
    const location = usePin ? pincode : `${city}, ${state}`;
    onSearch(location, entityType, instrumentType);
  };

  return (
    <form onSubmit={handleSubmit}>
      <div className="flex flex-wrap items-end gap-3">

        {/* State */}
        <div className="min-w-[160px] flex-1">
          <FieldLabel>State</FieldLabel>
          <SelectField
            value={state}
            onChange={handleStateChange}
            options={STATE_LIST}
            placeholder="Select state"
            icon={
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round"
                  d="M3 21v-4m0 0V5a2 2 0 012-2h6.5l1 1H21l-3 6 3 6h-8.5l-1-1H5a2 2 0 00-2 2zm9-13.5V9" />
              </svg>
            }
          />
        </div>

        {/* City or Pincode */}
        <div className="min-w-[160px] flex-1">
          <div className="mb-1 flex items-center justify-between">
            <FieldLabel>{usePin ? "Pincode" : "City"}</FieldLabel>
            <button
              type="button"
              onClick={toggleMode}
              className="text-[11px] font-medium text-blue-500 hover:text-blue-700 transition"
            >
              {usePin ? "← Use City" : "Use Pincode →"}
            </button>
          </div>
          {usePin ? (
            <div className="relative">
              <svg
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400"
                fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round"
                  d="M7 20l4-16m2 16l4-16M6 9h14M4 15h14" />
              </svg>
              <input
                type="text"
                value={pincode}
                onChange={(e) => setPincode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder={state ? "Enter 6-digit pincode" : "Select state first"}
                disabled={!state}
                maxLength={6}
                className={`w-full rounded-lg border pl-9 pr-3 py-2.5 text-sm outline-none transition
                  ${!state
                    ? "cursor-not-allowed border-gray-200 bg-gray-50 text-gray-400 placeholder-gray-300"
                    : "border-gray-300 bg-white text-gray-900 placeholder-gray-400 hover:border-blue-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
                  }`}
              />
              {pincode.length === 6 && (
                <span className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-600">
                  PIN ✓
                </span>
              )}
            </div>
          ) : (
            <SelectField
              value={city}
              onChange={setCity}
              options={cities}
              placeholder={state ? "Select city" : "Select state first"}
              disabled={!state}
              icon={
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round"
                    d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              }
            />
          )}
        </div>

        {/* Divider */}
        <div className="hidden h-10 w-px self-end bg-gray-200 sm:block" />

        {/* Entity Type */}
        <div className="min-w-[140px] flex-1">
          <FieldLabel>Entity Type</FieldLabel>
          <SelectField
            value={entityType}
            onChange={setEntityType}
            options={ENTITY_TYPES}
            icon={
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round"
                  d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
              </svg>
            }
          />
        </div>

        {/* Instrument */}
        <div className="min-w-[150px] flex-1">
          <FieldLabel>Instrument</FieldLabel>
          <SelectField
            value={instrumentType}
            onChange={setInstrumentType}
            options={INSTRUMENTS}
            icon={
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round"
                  d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
              </svg>
            }
          />
        </div>

        {/* Submit */}
        <div className="self-end">
          <button
            type="submit"
            disabled={loading || !isValid}
            className="flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold
              text-white shadow-sm transition hover:bg-blue-700 active:scale-95
              disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? (
              <>
                <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Scanning...
              </>
            ) : (
              <>
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                Find Leads
              </>
            )}
          </button>
        </div>

      </div>
    </form>
  );
}
