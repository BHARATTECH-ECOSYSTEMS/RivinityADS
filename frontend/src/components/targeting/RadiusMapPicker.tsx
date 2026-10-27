import { useState, useEffect, useId, type MouseEvent } from "react";
import {
  type RadiusUnit,
  MIN_RADIUS_METERS,
  MAX_RADIUS_METERS,
  convertRadiusToMeters,
  convertMetersToUnit,
} from "../../api/client";
import { Button, Input, Select, Alert } from "../ui";

export interface RadiusDraft {
  latitude: number | "";
  longitude: number | "";
  radiusValue: number | "";
  radiusUnit: RadiusUnit;
  name: string;
  country: string;
}

export interface PresetLocation {
  name: string;
  label: string;
  latitude: number;
  longitude: number;
  country: string;
}

export const PRESET_LOCATIONS: PresetLocation[] = [
  { name: "Mumbai", label: "Mumbai (IN)", latitude: 19.076, longitude: 72.8777, country: "IN" },
  { name: "Delhi NCR", label: "Delhi NCR (IN)", latitude: 28.6139, longitude: 77.209, country: "IN" },
  { name: "Bengaluru", label: "Bengaluru (IN)", latitude: 12.9716, longitude: 77.5946, country: "IN" },
  { name: "London", label: "London (GB)", latitude: 51.5074, longitude: -0.1278, country: "GB" },
  { name: "New York", label: "New York (US)", latitude: 40.7128, longitude: -74.006, country: "US" },
  { name: "San Francisco", label: "San Francisco (US)", latitude: 37.7749, longitude: -122.4194, country: "US" },
  { name: "Tokyo", label: "Tokyo (JP)", latitude: 35.6762, longitude: 139.6503, country: "JP" },
  { name: "Singapore", label: "Singapore (SG)", latitude: 1.3521, longitude: 103.8198, country: "SG" },
];

interface RadiusMapPickerProps {
  onAddRadiusTarget: (
    entry: {
      latitude: number;
      longitude: number;
      radius_meters: number;
      radius_unit: RadiusUnit;
      name?: string | null;
      country?: string | null;
    },
    mode: "include" | "exclude"
  ) => void;
  editingTarget?: {
    entry: {
      latitude: number;
      longitude: number;
      radius_meters: number;
      radius_unit?: RadiusUnit;
      name?: string | null;
      country?: string | null;
    };
    mode: "include" | "exclude";
  } | null;
  onUpdateRadiusTarget?: (
    entry: {
      latitude: number;
      longitude: number;
      radius_meters: number;
      radius_unit: RadiusUnit;
      name?: string | null;
      country?: string | null;
    },
    mode: "include" | "exclude"
  ) => void;
  onCancelEdit?: () => void;
  disabled?: boolean;
}

export function RadiusMapPicker({
  onAddRadiusTarget,
  editingTarget,
  onUpdateRadiusTarget,
  onCancelEdit,
  disabled,
}: RadiusMapPickerProps) {
  const [activeTab, setActiveTab] = useState<"map" | "coords">("map");

  // Form draft state
  const [latitude, setLatitude] = useState<number | "">(19.076);
  const [longitude, setLongitude] = useState<number | "">(72.8777);
  const [radiusValue, setRadiusValue] = useState<number | "">(5);
  const [radiusUnit, setRadiusUnit] = useState<RadiusUnit>("km");
  const [name, setName] = useState("");
  const [country, setCountry] = useState("IN");

  // Synchronize draft form state when entering or changing edit target
  useEffect(() => {
    if (editingTarget) {
      const e = editingTarget.entry;
      setLatitude(e.latitude);
      setLongitude(e.longitude);
      const unit = e.radius_unit || "km";
      setRadiusUnit(unit);
      setRadiusValue(convertMetersToUnit(e.radius_meters, unit));
      setName(e.name || "");
      setCountry(e.country || "");
      setPanX(0);
      setPanY(0);
      setValidationError(null);
    }
  }, [editingTarget]);

  // Map viewport state
  const [zoom, setZoom] = useState(1);
  const [panX, setPanX] = useState(0);
  const [panY, setPanY] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  const latInputId = useId();
  const lonInputId = useId();
  const radiusInputId = useId();

  // Derived canonical values
  const numLat = typeof latitude === "number" ? latitude : null;
  const numLon = typeof longitude === "number" ? longitude : null;
  const numRadius = typeof radiusValue === "number" ? radiusValue : null;

  const canonicalRadiusMeters =
    numRadius !== null ? convertRadiusToMeters(numRadius, radiusUnit) : null;

  // Validation checks
  const validate = (): string | null => {
    if (numLat === null || isNaN(numLat)) {
      return "Latitude is required and must be a valid number.";
    }
    if (numLat < -90 || numLat > 90) {
      return `Latitude must be between -90.0° and +90.0° (got ${numLat}°).`;
    }
    if (numLon === null || isNaN(numLon)) {
      return "Longitude is required and must be a valid number.";
    }
    if (numLon < -180 || numLon > 180) {
      return `Longitude must be between -180.0° and +180.0° (got ${numLon}°).`;
    }
    if (canonicalRadiusMeters === null || isNaN(canonicalRadiusMeters)) {
      return "Targeting radius is required and must be a positive number.";
    }
    if (canonicalRadiusMeters < MIN_RADIUS_METERS) {
      return `Radius must be at least ${MIN_RADIUS_METERS} meters (0.1 km). Sub-100m fences cause delivery instability.`;
    }
    if (canonicalRadiusMeters > MAX_RADIUS_METERS) {
      return `Radius must not exceed ${MAX_RADIUS_METERS.toLocaleString()} meters (100 km). For broader areas, use administrative targeting.`;
    }
    if (country.trim()) {
      const cleanCountry = country.trim().toUpperCase();
      if (cleanCountry.length !== 2 || !/^[A-Z]{2}$/.test(cleanCountry)) {
        return "Country scope must be a valid 2-letter ISO code (e.g., 'IN', 'US') or left blank.";
      }
    }
    return null;
  };

  const handleAdd = (mode: "include" | "exclude") => {
    const err = validate();
    if (err) {
      setValidationError(err);
      return;
    }
    setValidationError(null);

    if (numLat !== null && numLon !== null && canonicalRadiusMeters !== null) {
      onAddRadiusTarget(
        {
          latitude: Math.round(numLat * 1000000) / 1000000,
          longitude: Math.round(numLon * 1000000) / 1000000,
          radius_meters: canonicalRadiusMeters,
          radius_unit: radiusUnit,
          name: name.trim() ? name.trim() : null,
          country: country.trim() ? country.trim().toUpperCase() : null,
        },
        mode
      );
    }
  };

  const handleUpdate = (targetMode: "include" | "exclude") => {
    const err = validate();
    if (err) {
      setValidationError(err);
      return;
    }
    setValidationError(null);

    if (numLat !== null && numLon !== null && canonicalRadiusMeters !== null && onUpdateRadiusTarget) {
      onUpdateRadiusTarget(
        {
          latitude: Math.round(numLat * 1000000) / 1000000,
          longitude: Math.round(numLon * 1000000) / 1000000,
          radius_meters: canonicalRadiusMeters,
          radius_unit: radiusUnit,
          name: name.trim() ? name.trim() : null,
          country: country.trim() ? country.trim().toUpperCase() : null,
        },
        targetMode
      );
    }
  };

  const handleSelectPreset = (preset: PresetLocation) => {
    setLatitude(preset.latitude);
    setLongitude(preset.longitude);
    setCountry(preset.country);
    setName(preset.name);
    setPanX(0);
    setPanY(0);
    setValidationError(null);
  };

  const handleNudge = (dLat: number, dLon: number) => {
    const curLat = typeof latitude === "number" ? latitude : 0;
    const curLon = typeof longitude === "number" ? longitude : 0;
    const nextLat = Math.max(-90, Math.min(90, Math.round((curLat + dLat) * 10000) / 10000));
    let nextLon = Math.round((curLon + dLon) * 10000) / 10000;
    if (nextLon > 180) nextLon -= 360;
    if (nextLon < -180) nextLon += 360;
    setLatitude(nextLat);
    setLongitude(nextLon);
    setValidationError(null);
  };

  // Convert lat/lon to SVG canvas coordinates (viewBox 0 0 600 300)
  // Equirectangular projection: x = (lon + 180) / 360 * 600, y = (90 - lat) / 180 * 300
  const latToY = (lat: number) => ((90 - lat) / 180) * 300;
  const lonToX = (lon: number) => ((lon + 180) / 360) * 600;

  const yToLat = (y: number) => 90 - (y / 300) * 180;
  const xToLon = (x: number) => (x / 600) * 360 - 180;

  const curLat = numLat ?? 0;
  const curLon = numLon ?? 0;
  const markerX = lonToX(curLon);
  const markerY = latToY(curLat);

  // Approximate pixel radius on equirectangular map:
  // 1 degree latitude ~= 111.139 km. 300px for 180 degrees => ~1.666 px per degree ~= 0.015 px per km.
  // With zoom multiplier:
  const radiusKm = canonicalRadiusMeters ? canonicalRadiusMeters / 1000 : 5;
  const basePixelRadius = Math.max(8, (radiusKm / 111.139) * (300 / 180) * 60 * zoom);
  const displayRadiusPx = Math.min(140, basePixelRadius);

  const handleMapClick = (e: MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;

    const svgX = (clickX / rect.width) * 600;
    const svgY = (clickY / rect.height) * 300;

    // Apply inverse zoom/pan transformation
    const centerX = markerX;
    const centerY = markerY;
    const rawX = (svgX - 300) / zoom + centerX - panX;
    const rawY = (svgY - 150) / zoom + centerY - panY;

    const newLon = Math.max(-180, Math.min(180, Math.round(xToLon(rawX) * 10000) / 10000));
    const newLat = Math.max(-90, Math.min(90, Math.round(yToLat(rawY) * 10000) / 10000));

    setLatitude(newLat);
    setLongitude(newLon);
    setValidationError(null);
  };

  const coverageAreaKm2 = canonicalRadiusMeters
    ? Math.round(Math.PI * Math.pow(canonicalRadiusMeters / 1000, 2) * 10) / 10
    : 0;

  return (
    <div
      style={{
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-lg)",
        padding: "var(--space-4)",
        backgroundColor: "var(--color-bg)",
      }}
    >
      {/* Header and Mode Toggle */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "var(--space-3)",
          flexWrap: "wrap",
          gap: "var(--space-2)",
        }}
      >
        <div>
          <h4
            style={{
              fontSize: "var(--font-size-sm)",
              fontWeight: "var(--font-weight-bold)",
              margin: 0,
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
            }}
          >
            <span>🎯 Hyperlocal Radius Geofencing</span>
            <span
              className="badge badge-subtle"
              style={{ fontSize: "10px", padding: "2px 6px" }}
            >
              100m – 100km
            </span>
          </h4>
          <p
            className="text-muted"
            style={{ fontSize: "var(--font-size-xs)", margin: "var(--space-1) 0 0 0" }}
          >
            Define a high-precision circular geofence around target business coordinates.
          </p>
        </div>

        {/* Accessibility Tab Switcher */}
        <div style={{ display: "inline-flex", background: "var(--color-bg-subtle)", borderRadius: "var(--radius-md)", padding: "2px" }}>
          <button
            type="button"
            onClick={() => setActiveTab("map")}
            style={{
              padding: "4px 10px",
              fontSize: "var(--font-size-xs)",
              fontWeight: activeTab === "map" ? "var(--font-weight-semibold)" : "var(--font-weight-normal)",
              background: activeTab === "map" ? "var(--color-surface)" : "transparent",
              color: activeTab === "map" ? "var(--color-primary)" : "var(--color-text-muted)",
              border: "none",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              boxShadow: activeTab === "map" ? "var(--shadow-xs)" : "none",
            }}
            aria-pressed={activeTab === "map"}
          >
            🗺️ Interactive Map
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("coords")}
            style={{
              padding: "4px 10px",
              fontSize: "var(--font-size-xs)",
              fontWeight: activeTab === "coords" ? "var(--font-weight-semibold)" : "var(--font-weight-normal)",
              background: activeTab === "coords" ? "var(--color-surface)" : "transparent",
              color: activeTab === "coords" ? "var(--color-primary)" : "var(--color-text-muted)",
              border: "none",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              boxShadow: activeTab === "coords" ? "var(--shadow-xs)" : "none",
            }}
            aria-pressed={activeTab === "coords"}
          >
            📐 Precision Inputs
          </button>
        </div>
      </div>

      {/* Editing Target State Banner */}
      {editingTarget && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            background: "rgba(245, 158, 11, 0.1)",
            border: "1px solid rgba(245, 158, 11, 0.3)",
            borderRadius: "var(--radius-md)",
            padding: "var(--space-2) var(--space-3)",
            marginBottom: "var(--space-3)",
            fontSize: "var(--font-size-xs)",
            color: "var(--color-warning-text, #b45309)",
          }}
        >
          <span>
            ✏️ <strong>Editing {editingTarget.mode === "include" ? "Included" : "Excluded"} Target:</strong>{" "}
            {editingTarget.entry.name || `${editingTarget.entry.latitude}, ${editingTarget.entry.longitude}`}
          </span>
          {onCancelEdit && (
            <button
              type="button"
              onClick={onCancelEdit}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--color-text-muted)",
                cursor: "pointer",
                fontSize: "var(--font-size-xs)",
                textDecoration: "underline",
              }}
            >
              Cancel Edit
            </button>
          )}
        </div>
      )}

      {/* Preset Quick-Jump Anchors */}
      <div style={{ marginBottom: "var(--space-3)" }}>
        <div
          style={{
            fontSize: "11px",
            color: "var(--color-text-muted)",
            marginBottom: "var(--space-1)",
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <span>Quick Preset Hubs:</span>
          <span>Click to center pin</span>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-1)" }}>
          {PRESET_LOCATIONS.map((p) => {
            const isMatch =
              numLat !== null &&
              numLon !== null &&
              Math.abs(numLat - p.latitude) < 0.01 &&
              Math.abs(numLon - p.longitude) < 0.01;
            return (
              <button
                key={p.name}
                type="button"
                onClick={() => handleSelectPreset(p)}
                style={{
                  fontSize: "11px",
                  padding: "2px 8px",
                  borderRadius: "var(--radius-sm)",
                  border: isMatch ? "1px solid var(--color-primary)" : "1px solid var(--color-border)",
                  background: isMatch ? "var(--color-primary-subtle, rgba(37, 99, 235, 0.1))" : "var(--color-surface)",
                  color: isMatch ? "var(--color-primary)" : "var(--color-text)",
                  cursor: "pointer",
                }}
              >
                {p.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Interactive Visual Map Canvas */}
      {activeTab === "map" && (
        <div
          style={{
            marginBottom: "var(--space-3)",
            position: "relative",
            background: "#0f172a",
            borderRadius: "var(--radius-md)",
            overflow: "hidden",
            boxShadow: "inset 0 2px 4px rgba(0, 0, 0, 0.4)",
          }}
        >
          <svg
            viewBox="0 0 600 300"
            style={{ width: "100%", height: "230px", display: "block", cursor: "crosshair" }}
            onClick={handleMapClick}
            aria-label="Interactive coordinate map. Click or drag to adjust center latitude and longitude."
            role="application"
          >
            <defs>
              {/* Radial gradient for geofence circle */}
              <radialGradient id="radiusGradient" cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor="#3b82f6" stopOpacity="0.35" />
                <stop offset="85%" stopColor="#2563eb" stopOpacity="0.15" />
                <stop offset="100%" stopColor="#1d4ed8" stopOpacity="0.4" />
              </radialGradient>
              {/* Grid pattern */}
              <pattern id="grid" width="60" height="30" patternUnits="userSpaceOnUse">
                <path d="M 60 0 L 0 0 0 30" fill="none" stroke="#1e293b" strokeWidth="0.8" />
              </pattern>
            </defs>

            {/* Background & Coordinate Grid */}
            <rect width="600" height="300" fill="#0f172a" />
            <rect width="600" height="300" fill="url(#grid)" />

            {/* Equator & Prime Meridian axes */}
            <line x1="0" y1="150" x2="600" y2="150" stroke="#334155" strokeWidth="1" strokeDasharray="3 3" />
            <line x1="300" y1="0" x2="300" y2="300" stroke="#334155" strokeWidth="1" strokeDasharray="3 3" />

            {/* World Reference Landmass Contours (Stylized SVG Outlines) */}
            <g fill="#1e293b" stroke="#334155" strokeWidth="0.5" opacity="0.8">
              {/* North America */}
              <path d="M 80,60 Q 120,40 160,50 Q 180,90 150,130 Q 110,140 90,100 Z" />
              {/* South America */}
              <path d="M 140,160 Q 170,170 180,210 Q 160,260 140,270 Q 130,220 140,160 Z" />
              {/* Europe & Africa */}
              <path d="M 270,70 Q 320,60 330,95 Q 310,120 280,100 Z" />
              <path d="M 280,130 Q 340,140 330,200 Q 310,250 280,230 Q 260,180 280,130 Z" />
              {/* Asia & India */}
              <path d="M 350,60 Q 480,50 490,110 Q 430,130 380,110 Z" />
              <path d="M 390,130 L 420,130 L 405,170 Z" />
              {/* Australia */}
              <path d="M 460,200 Q 520,200 510,240 Q 460,250 460,200 Z" />
            </g>

            {/* Transform Group Centered on Marker */}
            <g transform={`translate(300, 150) scale(${zoom}) translate(${panX - markerX}, ${panY - markerY})`}>
              {/* Geofence Circular Area */}
              <circle
                cx={markerX}
                cy={markerY}
                r={displayRadiusPx}
                fill="url(#radiusGradient)"
                stroke="#60a5fa"
                strokeWidth="1.5"
                strokeDasharray="4 2"
              />

              {/* Concentric Pulse Ring */}
              <circle
                cx={markerX}
                cy={markerY}
                r={displayRadiusPx * 0.4}
                fill="none"
                stroke="#93c5fd"
                strokeWidth="0.8"
                opacity="0.6"
              />

              {/* Center Pin Marker */}
              <g transform={`translate(${markerX}, ${markerY})`}>
                {/* Drop pin shadow */}
                <ellipse cx="0" cy="4" rx="5" ry="2.5" fill="#000000" opacity="0.5" />
                {/* Pin Head */}
                <circle cx="0" cy="-10" r="7" fill="#ef4444" stroke="#ffffff" strokeWidth="2" />
                <circle cx="0" cy="-10" r="2.5" fill="#ffffff" />
                {/* Pin Stem */}
                <path d="M 0,-3 L 0,3" stroke="#ef4444" strokeWidth="2.5" strokeLinecap="round" />
              </g>
            </g>

            {/* Coordinate Overlay HUD (Top-Left) */}
            <rect x="8" y="8" width="220" height="42" rx="4" fill="rgba(15, 23, 42, 0.85)" stroke="#334155" />
            <text x="16" y="24" fill="#94a3b8" fontSize="10" fontFamily="var(--font-mono)">
              CENTER: {numLat !== null ? numLat.toFixed(4) : "—"}°, {numLon !== null ? numLon.toFixed(4) : "—"}°
            </text>
            <text x="16" y="40" fill="#38bdf8" fontSize="10" fontFamily="var(--font-mono)">
              RADIUS: {canonicalRadiusMeters ? (canonicalRadiusMeters / 1000).toFixed(1) : "—"} km (~{coverageAreaKm2} km²)
            </text>
          </svg>

          {/* Map Viewport Controls (Zoom & Reset) */}
          <div
            style={{
              position: "absolute",
              right: "8px",
              top: "8px",
              display: "flex",
              flexDirection: "column",
              gap: "4px",
            }}
          >
            <button
              type="button"
              onClick={() => setZoom((z) => Math.min(8, z * 1.4))}
              style={{
                width: "26px",
                height: "26px",
                background: "rgba(15, 23, 42, 0.85)",
                color: "#ffffff",
                border: "1px solid #334155",
                borderRadius: "4px",
                cursor: "pointer",
                fontSize: "14px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
              title="Zoom In"
              aria-label="Zoom in on map"
            >
              +
            </button>
            <button
              type="button"
              onClick={() => setZoom((z) => Math.max(1, z / 1.4))}
              style={{
                width: "26px",
                height: "26px",
                background: "rgba(15, 23, 42, 0.85)",
                color: "#ffffff",
                border: "1px solid #334155",
                borderRadius: "4px",
                cursor: "pointer",
                fontSize: "14px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
              title="Zoom Out"
              aria-label="Zoom out on map"
            >
              −
            </button>
            <button
              type="button"
              onClick={() => {
                setZoom(1);
                setPanX(0);
                setPanY(0);
              }}
              style={{
                width: "26px",
                height: "26px",
                background: "rgba(15, 23, 42, 0.85)",
                color: "#94a3b8",
                border: "1px solid #334155",
                borderRadius: "4px",
                cursor: "pointer",
                fontSize: "10px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
              title="Reset View"
              aria-label="Reset map zoom"
            >
              1x
            </button>
          </div>

          {/* Micro-Nudge Keyboard/Click Controls */}
          <div
            style={{
              position: "absolute",
              right: "8px",
              bottom: "8px",
              display: "flex",
              alignItems: "center",
              gap: "2px",
              background: "rgba(15, 23, 42, 0.85)",
              padding: "2px 4px",
              borderRadius: "4px",
              border: "1px solid #334155",
            }}
          >
            <span style={{ fontSize: "10px", color: "#64748b", marginRight: "4px" }}>Nudge:</span>
            <button
              type="button"
              onClick={() => handleNudge(0.01, 0)}
              style={{ background: "none", border: "none", color: "#e2e8f0", cursor: "pointer", padding: "2px 4px", fontSize: "10px" }}
              title="Nudge North (+0.01°)"
              aria-label="Nudge North (+0.01° latitude)"
            >
              ▲
            </button>
            <button
              type="button"
              onClick={() => handleNudge(-0.01, 0)}
              style={{ background: "none", border: "none", color: "#e2e8f0", cursor: "pointer", padding: "2px 4px", fontSize: "10px" }}
              title="Nudge South (-0.01°)"
              aria-label="Nudge South (-0.01° latitude)"
            >
              ▼
            </button>
            <button
              type="button"
              onClick={() => handleNudge(0, -0.01)}
              style={{ background: "none", border: "none", color: "#e2e8f0", cursor: "pointer", padding: "2px 4px", fontSize: "10px" }}
              title="Nudge West (-0.01°)"
              aria-label="Nudge West (-0.01° longitude)"
            >
              ◀
            </button>
            <button
              type="button"
              onClick={() => handleNudge(0, 0.01)}
              style={{ background: "none", border: "none", color: "#e2e8f0", cursor: "pointer", padding: "2px 4px", fontSize: "10px" }}
              title="Nudge East (+0.01°)"
              aria-label="Nudge East (+0.01° longitude)"
            >
              ▶
            </button>
          </div>
        </div>
      )}

      {/* Geocoding Provider Blocker Notice */}
      <div
        style={{
          background: "var(--color-bg-subtle)",
          border: "1px solid var(--color-border-subtle)",
          borderRadius: "var(--radius-md)",
          padding: "var(--space-2) var(--space-3)",
          fontSize: "11px",
          color: "var(--color-text-muted)",
          marginBottom: "var(--space-3)",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
        }}
      >
        <span>📍</span>
        <span>
          <strong>Direct Coordinate Geofence:</strong> Address geocoding requires a dedicated backend geocoder service key (unconfigured). Enter decimal coordinates directly, pick a preset hub, or click the interactive map.
        </span>
      </div>

      {/* Primary Coordinates & Radius Inputs */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "var(--space-3)",
          marginBottom: "var(--space-3)",
        }}
      >
        <div>
          <label
            htmlFor={latInputId}
            style={{
              display: "block",
              fontSize: "var(--font-size-xs)",
              fontWeight: "var(--font-weight-medium)",
              marginBottom: "var(--space-1)",
            }}
          >
            Latitude (-90.0° to +90.0°) <span style={{ color: "var(--color-danger)" }}>*</span>
          </label>
          <input
            id={latInputId}
            type="number"
            step="any"
            min="-90"
            max="90"
            value={latitude}
            onChange={(e) => {
              const val = e.target.value === "" ? "" : parseFloat(e.target.value);
              setLatitude(val);
              setValidationError(null);
            }}
            placeholder="e.g. 19.0760"
            className="input"
            style={{ width: "100%" }}
            disabled={disabled}
            required
          />
        </div>

        <div>
          <label
            htmlFor={lonInputId}
            style={{
              display: "block",
              fontSize: "var(--font-size-xs)",
              fontWeight: "var(--font-weight-medium)",
              marginBottom: "var(--space-1)",
            }}
          >
            Longitude (-180.0° to +180.0°) <span style={{ color: "var(--color-danger)" }}>*</span>
          </label>
          <input
            id={lonInputId}
            type="number"
            step="any"
            min="-180"
            max="180"
            value={longitude}
            onChange={(e) => {
              const val = e.target.value === "" ? "" : parseFloat(e.target.value);
              setLongitude(val);
              setValidationError(null);
            }}
            placeholder="e.g. 72.8777"
            className="input"
            style={{ width: "100%" }}
            disabled={disabled}
            required
          />
        </div>
      </div>

      {/* Radius & Unit Selector */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "2fr 1fr",
          gap: "var(--space-3)",
          marginBottom: "var(--space-3)",
        }}
      >
        <div>
          <label
            htmlFor={radiusInputId}
            style={{
              display: "block",
              fontSize: "var(--font-size-xs)",
              fontWeight: "var(--font-weight-medium)",
              marginBottom: "var(--space-1)",
            }}
          >
            Radius Distance <span style={{ color: "var(--color-danger)" }}>*</span>
            {canonicalRadiusMeters !== null && (
              <span className="text-muted" style={{ marginLeft: "var(--space-2)", fontWeight: "normal" }}>
                (= {canonicalRadiusMeters.toLocaleString()} meters / ~{coverageAreaKm2} km²)
              </span>
            )}
          </label>
          <input
            id={radiusInputId}
            type="number"
            step={radiusUnit === "meters" ? "10" : "0.1"}
            min={radiusUnit === "meters" ? "100" : "0.1"}
            max={radiusUnit === "meters" ? "100000" : radiusUnit === "km" ? "100" : "62.1"}
            value={radiusValue}
            onChange={(e) => {
              const val = e.target.value === "" ? "" : parseFloat(e.target.value);
              setRadiusValue(val);
              setValidationError(null);
            }}
            placeholder={radiusUnit === "meters" ? "5000" : "5.0"}
            className="input"
            style={{ width: "100%" }}
            disabled={disabled}
            required
          />
        </div>

        <div>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-xs)",
              fontWeight: "var(--font-weight-medium)",
              marginBottom: "var(--space-1)",
            }}
          >
            Radius Unit
          </label>
          <select
            value={radiusUnit}
            onChange={(e) => {
              const nextUnit = e.target.value as RadiusUnit;
              // Smoothly convert radiusValue if a numeric value exists
              if (numRadius !== null && canonicalRadiusMeters !== null) {
                const converted = convertMetersToUnit(canonicalRadiusMeters, nextUnit);
                setRadiusValue(converted);
              }
              setRadiusUnit(nextUnit);
            }}
            className="select"
            style={{ width: "100%" }}
            disabled={disabled}
          >
            <option value="km">Kilometers (km)</option>
            <option value="meters">Meters (m)</option>
            <option value="miles">Miles (mi)</option>
          </select>
        </div>
      </div>

      {/* Optional Metadata: Target Label & Country Scope */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "2fr 1fr",
          gap: "var(--space-3)",
          marginBottom: "var(--space-3)",
        }}
      >
        <div>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-xs)",
              fontWeight: "var(--font-weight-medium)",
              marginBottom: "var(--space-1)",
            }}
          >
            Target Name / Label (Optional)
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Flagship Store Downtown, Tech Park"
            className="input"
            style={{ width: "100%" }}
            disabled={disabled}
          />
        </div>

        <div>
          <label
            style={{
              display: "block",
              fontSize: "var(--font-size-xs)",
              fontWeight: "var(--font-weight-medium)",
              marginBottom: "var(--space-1)",
            }}
          >
            Country Scope (Optional)
          </label>
          <input
            type="text"
            maxLength={2}
            value={country}
            onChange={(e) => {
              setCountry(e.target.value.toUpperCase());
              setValidationError(null);
            }}
            placeholder="e.g. IN, US"
            className="input"
            style={{ width: "100%", textTransform: "uppercase" }}
            disabled={disabled}
          />
        </div>
      </div>

      {/* Validation Error Banner */}
      {validationError && (
        <Alert variant="error" style={{ marginBottom: "var(--space-3)" }}>
          {validationError}
        </Alert>
      )}

      {/* Privacy & Provenance Safeguard Notice */}
      <div
        style={{
          background: "rgba(37, 99, 235, 0.05)",
          border: "1px solid rgba(37, 99, 235, 0.2)",
          borderRadius: "var(--radius-md)",
          padding: "var(--space-2) var(--space-3)",
          fontSize: "11px",
          color: "var(--color-primary-text, #1e40af)",
          marginBottom: "var(--space-4)",
          lineHeight: "1.4",
        }}
      >
        🔒 <strong>Location Privacy & Serving Rules:</strong> Hyperlocal radius delivery requires explicit ad viewer consent and verified GPS device signal accuracy. Deliveries strictly fail-closed when only approximate IP-derived location is available.
      </div>

      {/* Action Buttons: Edit mode vs Add mode */}
      {editingTarget ? (
        <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", alignItems: "center" }}>
          <Button
            variant="primary"
            size="sm"
            type="button"
            disabled={disabled}
            onClick={() => handleUpdate(editingTarget.mode)}
          >
            ✓ Update {editingTarget.mode === "include" ? "Included" : "Excluded"} Target
          </Button>
          <Button
            variant="outline"
            size="sm"
            type="button"
            disabled={disabled}
            onClick={() => handleUpdate(editingTarget.mode === "include" ? "exclude" : "include")}
          >
            Switch to {editingTarget.mode === "include" ? "Exclude" : "Include"} & Update
          </Button>
          {onCancelEdit && (
            <Button
              variant="ghost"
              size="sm"
              type="button"
              disabled={disabled}
              onClick={onCancelEdit}
            >
              Cancel Edit
            </Button>
          )}
        </div>
      ) : (
        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          <Button
            variant="outline"
            size="sm"
            type="button"
            disabled={disabled}
            onClick={() => handleAdd("include")}
            style={{ borderColor: "var(--color-success)", color: "var(--color-success)" }}
          >
            + Include Radius Target
          </Button>
          <Button
            variant="outline"
            size="sm"
            type="button"
            disabled={disabled}
            onClick={() => handleAdd("exclude")}
            style={{ borderColor: "var(--color-danger)", color: "var(--color-danger)" }}
          >
            - Exclude Radius Target
          </Button>
        </div>
      )}
    </div>
  );
}
