import React, { useEffect, useRef, useState } from "react";
import {
  MapContainer, TileLayer, Marker, Popup,
  ZoomControl, useMap,
} from "react-leaflet";
import L from "leaflet";

const ENTITY_COLORS = {
  "Bank":             "#3b82f6",
  "NBFC":             "#8b5cf6",
  "Corporate":        "#10b981",
  "Financial Entity": "#6b7280",
};
const BRANCH_COLOR = "#06b6d4";
const HQ_COLOR     = "#f97316";

// ── Tile layers (all 100% free, no API key) ───────────────────────────────────
const TILES = {
  hybrid: {
    label: "🌍 Earth",
    layers: [
      // Satellite base
      {
        url:  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr: "Tiles &copy; Esri — Source: Esri, USGS, NOAA",
        maxNativeZoom: 19,
        maxZoom: 22,
      },
      // Place-name / road labels on top
      {
        url:  "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        attr: "",
        maxNativeZoom: 19,
        maxZoom: 22,
        opacity: 1,
      },
    ],
  },
  satellite: {
    label: "🛰 Satellite",
    layers: [
      {
        url:  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr: "Tiles &copy; Esri — Source: Esri, USGS, NOAA",
        maxNativeZoom: 19,
        maxZoom: 22,
      },
    ],
  },
  street: {
    label: "🗺 Street",
    layers: [
      {
        url:  "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        attr: "&copy; OpenStreetMap &copy; CARTO",
        subdomains: "abcd",
        maxNativeZoom: 19,
        maxZoom: 22,
      },
    ],
  },
};

// ── SVG pin markers ───────────────────────────────────────────────────────────
function makePin(color, w = 30, h = 39) {
  const svg = `<svg width="${w}" height="${h}" viewBox="0 0 30 39" xmlns="http://www.w3.org/2000/svg">
    <filter id="ds${color.replace("#","")}">
      <feDropShadow dx="0" dy="2" stdDeviation="2" flood-color="rgba(0,0,0,0.4)"/>
    </filter>
    <path d="M15 1C8.37 1 3 6.37 3 13c0 8.6 12 25 12 25S27 21.6 27 13C27 6.37 21.63 1 15 1z"
      fill="${color}" filter="url(#ds${color.replace("#","")})"/>
    <circle cx="15" cy="13" r="5.5" fill="white" opacity="0.92"/>
  </svg>`;
  return L.divIcon({
    html: svg, className: "",
    iconSize: [w, h], iconAnchor: [w / 2, h], popupAnchor: [0, -h + 4],
  });
}

function makeBranchPin(color) { return makePin(color, 20, 26); }

// ── Fly to city on search ─────────────────────────────────────────────────────
function FlyController({ lat, lng }) {
  const map  = useMap();
  const prev = useRef(null);
  useEffect(() => {
    if (!lat || !lng) return;
    const key = `${lat.toFixed(3)},${lng.toFixed(3)}`;
    if (prev.current === key) return;
    prev.current = key;
    map.flyTo([lat, lng], 12, { animate: true, duration: 1.2 });
  }, [lat, lng, map]);
  return null;
}

// ── Main component ────────────────────────────────────────────────────────────
export default function MapView({
  companies, cityLat, cityLng,
  selectedId, onSelectCompany,
  officeLocations,
}) {
  const [tileMode, setTileMode] = useState("hybrid"); // Earth view by default

  const tile = TILES[tileMode];

  return (
    <div style={{ position: "relative", height: "100%", width: "100%" }}>

      {/* ── Tile-mode toggle — top-right, above map ── */}
      <div style={{
        position: "absolute", top: 12, right: 12, zIndex: 1000,
        display: "flex", gap: 4,
      }}>
        {Object.entries(TILES).map(([key, t]) => (
          <button
            key={key}
            onClick={() => setTileMode(key)}
            style={{
              padding: "5px 10px",
              fontSize: 11,
              fontWeight: 600,
              borderRadius: 6,
              border: tileMode === key ? "2px solid #2563eb" : "2px solid rgba(255,255,255,0.5)",
              cursor: "pointer",
              background: tileMode === key ? "#2563eb" : "rgba(255,255,255,0.92)",
              color: tileMode === key ? "white" : "#374151",
              backdropFilter: "blur(4px)",
              boxShadow: "0 2px 6px rgba(0,0,0,0.25)",
              transition: "all 0.15s",
              whiteSpace: "nowrap",
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      <MapContainer
        center={[20.5937, 78.9629]}
        zoom={5}
        minZoom={4}
        maxZoom={22}
        scrollWheelZoom={true}
        doubleClickZoom={true}
        zoomControl={false}          // we add our own below, positioned bottom-right
        style={{ height: "100%", width: "100%" }}
      >
        {/* Zoom controls — bottom-right, clearly visible over satellite */}
        <ZoomControl position="bottomright" />

        {/* Render tile layers for active mode */}
        {tile.layers.map((layer, i) => (
          <TileLayer
            key={`${tileMode}-${i}`}
            url={layer.url}
            attribution={layer.attr}
            subdomains={layer.subdomains || "abc"}
            maxNativeZoom={layer.maxNativeZoom ?? 19}
            maxZoom={layer.maxZoom ?? 22}
            opacity={layer.opacity ?? 1}
          />
        ))}

        {/* Fly to searched city */}
        {cityLat && cityLat !== 20.5937 && (
          <FlyController lat={cityLat} lng={cityLng} />
        )}

        {/* Company pins */}
        {companies.map((company) => {
          const color      = ENTITY_COLORS[company.entity_type] ?? ENTITY_COLORS["Financial Entity"];
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
                    {company.score_label && (
                      <span style={{ color, fontSize: 11, fontWeight: 600 }}>
                        {company.score_label} · {company.score}
                      </span>
                    )}
                  </div>
                  {company.address && (
                    <div style={{ color: "#6b7280", fontSize: 11, marginTop: 4 }}>
                      {company.address.split(",").slice(0, 2).join(",")}
                    </div>
                  )}
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
    </div>
  );
}
