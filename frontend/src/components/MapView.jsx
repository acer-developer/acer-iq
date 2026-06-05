import React, { useEffect, useRef } from "react";
import { MapContainer, TileLayer, Marker, Popup, useMap } from "react-leaflet";
import L from "leaflet";

const ENTITY_COLORS = {
  "Bank":             "#3b82f6",
  "NBFC":             "#8b5cf6",
  "Corporate":        "#10b981",
  "Financial Entity": "#6b7280",
};

const BRANCH_COLOR  = "#06b6d4";
const HQ_COLOR      = "#f97316";

function makePin(color, w = 30, h = 39) {
  const svg = `<svg width="${w}" height="${h}" viewBox="0 0 30 39" xmlns="http://www.w3.org/2000/svg">
    <filter id="ds"><feDropShadow dx="0" dy="2" stdDeviation="2" flood-color="rgba(0,0,0,0.22)"/></filter>
    <path d="M15 1C8.37 1 3 6.37 3 13c0 8.6 12 25 12 25S27 21.6 27 13C27 6.37 21.63 1 15 1z"
      fill="${color}" filter="url(#ds)"/>
    <circle cx="15" cy="13" r="5.5" fill="white" opacity="0.92"/>
  </svg>`;
  return L.divIcon({
    html: svg,
    className: "",
    iconSize:   [w, h],
    iconAnchor: [w / 2, h],
    popupAnchor:[0, -h + 4],
  });
}

function makeBranchPin(color) {
  return makePin(color, 20, 26);
}

// Flies to new city coordinates when they change
function FlyController({ lat, lng }) {
  const map = useMap();
  const prev = useRef(null);
  useEffect(() => {
    if (!lat || !lng) return;
    const key = `${lat.toFixed(4)},${lng.toFixed(4)}`;
    if (prev.current === key) return;
    prev.current = key;
    map.flyTo([lat, lng], 12, { animate: true, duration: 1.2 });
  }, [lat, lng, map]);
  return null;
}

export default function MapView({
  companies, cityLat, cityLng,
  selectedId, onSelectCompany,
  officeLocations,
}) {
  return (
    <MapContainer
      center={[20.5937, 78.9629]}
      zoom={5}
      style={{ height: "100%", width: "100%" }}
      zoomControl
    >
      {/* Clean light tiles — no API key */}
      <TileLayer
        url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
        attribution='&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'
        subdomains="abcd"
        maxZoom={20}
      />

      {/* Animate zoom when city changes */}
      {cityLat && <FlyController lat={cityLat} lng={cityLng} />}

      {/* Company pins */}
      {companies.map((company) => {
        const color     = ENTITY_COLORS[company.entity_type] ?? ENTITY_COLORS["Financial Entity"];
        const isSelected = company.id === selectedId;
        const w = isSelected ? 36 : 30;
        const h = isSelected ? 47 : 39;
        return (
          <Marker
            key={company.id}
            position={[company.lat, company.lng]}
            icon={makePin(color, w, h)}
            zIndexOffset={isSelected ? 1000 : 0}
            eventHandlers={{ click: () => onSelectCompany(company.id) }}
          >
            <Popup>
              <div style={{ minWidth: 190, fontFamily: "Inter, sans-serif" }}>
                <div style={{ fontWeight: 700, fontSize: 13, color: "#111827" }}>
                  {company.name}
                </div>
                <div style={{ marginTop: 4, display: "flex", gap: 6, alignItems: "center" }}>
                  {company.entity_type && (
                    <span style={{
                      background: color + "18", color, border: `1px solid ${color}44`,
                      borderRadius: 4, padding: "1px 6px", fontSize: 10, fontWeight: 700,
                    }}>
                      {company.entity_type}
                    </span>
                  )}
                  <span style={{ color, fontSize: 11, fontWeight: 600 }}>
                    {company.score_label} · {company.score}
                  </span>
                </div>
                <div style={{ color: "#6b7280", fontSize: 11, marginTop: 4 }}>
                  {company.address?.split(",").slice(0, 2).join(",")}
                </div>
              </div>
            </Popup>
          </Marker>
        );
      })}

      {/* Branch / office pins */}
      {officeLocations?.map((office, i) => {
        const isHQ = office.location_type === "HQ" || office.location_type === "Head Office";
        return (
          <Marker
            key={`office-${i}`}
            position={[office.lat, office.lng]}
            icon={isHQ ? makePin(HQ_COLOR) : makeBranchPin(BRANCH_COLOR)}
          >
            <Popup>
              <div style={{ minWidth: 160, fontFamily: "Inter, sans-serif" }}>
                <div style={{ fontWeight: 600, fontSize: 12, color: "#111827" }}>
                  {office.name}
                </div>
                <div style={{ color: isHQ ? HQ_COLOR : BRANCH_COLOR, fontSize: 10, fontWeight: 600, marginTop: 2 }}>
                  {office.location_type}
                </div>
                <div style={{ color: "#6b7280", fontSize: 10, marginTop: 3 }}>
                  {office.address?.split(",").slice(0, 2).join(",")}
                </div>
              </div>
            </Popup>
          </Marker>
        );
      })}
    </MapContainer>
  );
}
