import { useState, useEffect } from "react";
import {
  type GeoEntry,
  type RadiusUnit,
  formatGeoEntry,
  isRadiusGeoEntry,
  getReferenceCountries,
  getReferenceStates,
  getReferenceCities,
  getReferencePincodes,
} from "../../api/client";
import { RadiusMapPicker } from "./RadiusMapPicker";
import { Button, Select, Alert } from "../ui";

interface TargetingGeoPanelProps {
  geoInclude: GeoEntry[];
  geoExclude: GeoEntry[];
  onChange: (include: GeoEntry[], exclude: GeoEntry[]) => void;
  disabled?: boolean;
}

export function TargetingGeoPanel({
  geoInclude,
  geoExclude,
  onChange,
  disabled,
}: TargetingGeoPanelProps) {
  const [geoMode, setGeoMode] = useState<"administrative" | "radius">("administrative");
  const [panelError, setPanelError] = useState<string | null>(null);
  const [editingTarget, setEditingTarget] = useState<{
    entry: GeoEntry;
    mode: "include" | "exclude";
    index: number;
  } | null>(null);

  // Administrative selection state
  const [countries, setCountries] = useState<string[]>([]);
  const [states, setStates] = useState<{ code: string; name: string }[]>([]);
  const [cities, setCities] = useState<string[]>([]);
  const [pincodes, setPincodes] = useState<string[]>([]);

  const [selCountry, setSelCountry] = useState("");
  const [selState, setSelState] = useState("");
  const [selCity, setSelCity] = useState("");
  const [selPincode, setSelPincode] = useState("");

  // Load countries on mount
  useEffect(() => {
    void getReferenceCountries().then(setCountries).catch(() => {});
  }, []);

  // Update states when country changes
  useEffect(() => {
    setSelState("");
    setSelCity("");
    setSelPincode("");
    if (selCountry) {
      void getReferenceStates(selCountry).then(setStates).catch(() => setStates([]));
    } else {
      setStates([]);
    }
  }, [selCountry]);

  // Update cities when state changes
  useEffect(() => {
    setSelCity("");
    setSelPincode("");
    if (selCountry && selState) {
      void getReferenceCities(selCountry, selState).then(setCities).catch(() => setCities([]));
    } else {
      setCities([]);
    }
  }, [selState, selCountry]);

  // Update pincodes when city changes
  useEffect(() => {
    setSelPincode("");
    if (selCountry && selState && selCity) {
      void getReferencePincodes(selCountry, selState, selCity).then(setPincodes).catch(() => setPincodes([]));
    } else {
      setPincodes([]);
    }
  }, [selCity, selState, selCountry]);

  // Comparison helper for GeoEntry deduplication
  const areEntriesEqual = (a: GeoEntry, b: GeoEntry): boolean => {
    const isARadius = isRadiusGeoEntry(a);
    const isBRadius = isRadiusGeoEntry(b);

    if (isARadius && isBRadius) {
      return (
        Number(a.latitude).toFixed(6) === Number(b.latitude).toFixed(6) &&
        Number(a.longitude).toFixed(6) === Number(b.longitude).toFixed(6) &&
        Number(a.radius_meters || 0).toFixed(1) === Number(b.radius_meters || 0).toFixed(1) &&
        (a.country || "").toUpperCase() === (b.country || "").toUpperCase()
      );
    }

    if (!isARadius && !isBRadius) {
      return (
        (a.country || "").toUpperCase() === (b.country || "").toUpperCase() &&
        (a.state || "") === (b.state || "") &&
        (a.city || "") === (b.city || "") &&
        (a.pincode || "") === (b.pincode || "")
      );
    }

    return false;
  };

  const handleStartEdit = (mode: "include" | "exclude", idx: number) => {
    const list = mode === "include" ? geoInclude : geoExclude;
    const item = list[idx];
    if (!item) return;
    setPanelError(null);
    setEditingTarget({ entry: item, mode, index: idx });
    if (isRadiusGeoEntry(item)) {
      setGeoMode("radius");
    } else {
      setGeoMode("administrative");
      setSelCountry(item.country || "");
      setSelState(item.state || "");
      setSelCity(item.city || "");
      setSelPincode(item.pincode || "");
    }
  };

  const handleCancelEdit = () => {
    setEditingTarget(null);
    setPanelError(null);
    setSelCountry("");
    setSelState("");
    setSelCity("");
    setSelPincode("");
  };

  const handleAddAdminGeo = (type: "include" | "exclude") => {
    if (!selCountry) return;
    setPanelError(null);

    const entry: GeoEntry = { country: selCountry.toUpperCase() };
    if (selState) entry.state = selState;
    if (selCity) entry.city = selCity;
    if (selPincode) entry.pincode = selPincode;

    const targetList = type === "include" ? geoInclude : geoExclude;
    const opposingList = type === "include" ? geoExclude : geoInclude;

    // Check conflict
    if (opposingList.some((e) => areEntriesEqual(e, entry))) {
      setPanelError(
        `Conflicting targeting rule: this location is already in the ${
          type === "include" ? "excluded" : "included"
        } list.`
      );
      return;
    }

    // Check duplicate
    if (targetList.some((e) => areEntriesEqual(e, entry))) {
      setPanelError("This location is already added to this targeting list.");
      return;
    }

    if (type === "include") {
      onChange([...geoInclude, entry], geoExclude);
    } else {
      onChange(geoInclude, [...geoExclude, entry]);
    }

    // Clear sub-selections
    setSelCountry("");
    setSelState("");
    setSelCity("");
    setSelPincode("");
  };

  const handleUpdateAdminGeo = (newMode: "include" | "exclude") => {
    if (!editingTarget || !selCountry) return;
    setPanelError(null);

    const entry: GeoEntry = { country: selCountry.toUpperCase() };
    if (selState) entry.state = selState;
    if (selCity) entry.city = selCity;
    if (selPincode) entry.pincode = selPincode;

    const opposingList = (newMode === "include" ? geoExclude : geoInclude).filter(
      (_, i) => !(editingTarget.mode !== newMode && i === editingTarget.index)
    );
    if (opposingList.some((e) => areEntriesEqual(e, entry))) {
      setPanelError(
        `Conflicting targeting rule: this location is already in the ${
          newMode === "include" ? "excluded" : "included"
        } list.`
      );
      return;
    }

    const targetList = (newMode === "include" ? geoInclude : geoExclude).filter(
      (_, i) => !(editingTarget.mode === newMode && i === editingTarget.index)
    );
    if (targetList.some((e) => areEntriesEqual(e, entry))) {
      setPanelError("This location is already added to this targeting list.");
      return;
    }

    let nextInclude = [...geoInclude];
    let nextExclude = [...geoExclude];

    if (editingTarget.mode === "include") {
      nextInclude.splice(editingTarget.index, 1);
    } else {
      nextExclude.splice(editingTarget.index, 1);
    }

    if (newMode === "include") {
      nextInclude.push(entry);
    } else {
      nextExclude.push(entry);
    }

    onChange(nextInclude, nextExclude);
    handleCancelEdit();
  };

  const handleAddRadiusGeo = (
    radiusTarget: {
      latitude: number;
      longitude: number;
      radius_meters: number;
      radius_unit: RadiusUnit;
      name?: string | null;
      country?: string | null;
    },
    mode: "include" | "exclude"
  ) => {
    setPanelError(null);
    const entry: GeoEntry = {
      latitude: radiusTarget.latitude,
      longitude: radiusTarget.longitude,
      radius_meters: radiusTarget.radius_meters,
      radius_unit: radiusTarget.radius_unit,
      name: radiusTarget.name || null,
      country: radiusTarget.country ? radiusTarget.country.toUpperCase() : null,
    };

    const targetList = mode === "include" ? geoInclude : geoExclude;
    const opposingList = mode === "include" ? geoExclude : geoInclude;

    // Check conflict
    if (opposingList.some((e) => areEntriesEqual(e, entry))) {
      setPanelError(
        `Conflicting targeting rule: an identical radius target is already in the ${
          mode === "include" ? "excluded" : "included"
        } list.`
      );
      return;
    }

    // Check duplicate
    if (targetList.some((e) => areEntriesEqual(e, entry))) {
      setPanelError("An identical radius target is already present in this list.");
      return;
    }

    if (mode === "include") {
      onChange([...geoInclude, entry], geoExclude);
    } else {
      onChange(geoInclude, [...geoExclude, entry]);
    }
  };

  const handleUpdateRadiusGeo = (
    radiusTarget: {
      latitude: number;
      longitude: number;
      radius_meters: number;
      radius_unit: RadiusUnit;
      name?: string | null;
      country?: string | null;
    },
    newMode: "include" | "exclude"
  ) => {
    if (!editingTarget) return;
    setPanelError(null);
    const entry: GeoEntry = {
      latitude: radiusTarget.latitude,
      longitude: radiusTarget.longitude,
      radius_meters: radiusTarget.radius_meters,
      radius_unit: radiusTarget.radius_unit,
      name: radiusTarget.name || null,
      country: radiusTarget.country ? radiusTarget.country.toUpperCase() : null,
    };

    const opposingList = (newMode === "include" ? geoExclude : geoInclude).filter(
      (_, i) => !(editingTarget.mode !== newMode && i === editingTarget.index)
    );
    if (opposingList.some((e) => areEntriesEqual(e, entry))) {
      setPanelError(
        `Conflicting targeting rule: an identical radius target is already in the ${
          newMode === "include" ? "excluded" : "included"
        } list.`
      );
      return;
    }

    const targetList = (newMode === "include" ? geoInclude : geoExclude).filter(
      (_, i) => !(editingTarget.mode === newMode && i === editingTarget.index)
    );
    if (targetList.some((e) => areEntriesEqual(e, entry))) {
      setPanelError("An identical radius target is already present in this list.");
      return;
    }

    let nextInclude = [...geoInclude];
    let nextExclude = [...geoExclude];

    if (editingTarget.mode === "include") {
      nextInclude.splice(editingTarget.index, 1);
    } else {
      nextExclude.splice(editingTarget.index, 1);
    }

    if (newMode === "include") {
      nextInclude.push(entry);
    } else {
      nextExclude.push(entry);
    }

    onChange(nextInclude, nextExclude);
    handleCancelEdit();
  };

  const handleRemoveGeo = (type: "include" | "exclude", idx: number) => {
    setPanelError(null);
    if (editingTarget && editingTarget.mode === type && editingTarget.index === idx) {
      handleCancelEdit();
    } else if (editingTarget && editingTarget.mode === type && editingTarget.index > idx) {
      setEditingTarget({ ...editingTarget, index: editingTarget.index - 1 });
    }
    if (type === "include") {
      onChange(
        geoInclude.filter((_, i) => i !== idx),
        geoExclude
      );
    } else {
      onChange(
        geoInclude,
        geoExclude.filter((_, i) => i !== idx)
      );
    }
  };

  return (
    <div
      style={{
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-lg)",
        padding: "var(--space-4)",
      }}
    >
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
        <h4
          style={{
            fontSize: "var(--font-size-sm)",
            fontWeight: "var(--font-weight-bold)",
            margin: 0,
          }}
        >
          📍 Geographic Targeting
        </h4>

        {/* Mode Selector Tabs */}
        <div
          style={{
            display: "inline-flex",
            background: "var(--color-bg-subtle)",
            borderRadius: "var(--radius-md)",
            padding: "2px",
          }}
        >
          <button
            type="button"
            onClick={() => setGeoMode("administrative")}
            style={{
              padding: "4px 12px",
              fontSize: "var(--font-size-xs)",
              fontWeight:
                geoMode === "administrative"
                  ? "var(--font-weight-semibold)"
                  : "var(--font-weight-normal)",
              background:
                geoMode === "administrative" ? "var(--color-surface)" : "transparent",
              color:
                geoMode === "administrative"
                  ? "var(--color-primary)"
                  : "var(--color-text-muted)",
              border: "none",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              boxShadow: geoMode === "administrative" ? "var(--shadow-xs)" : "none",
            }}
            aria-pressed={geoMode === "administrative"}
          >
            🏛️ Administrative Regions
          </button>
          <button
            type="button"
            onClick={() => setGeoMode("radius")}
            style={{
              padding: "4px 12px",
              fontSize: "var(--font-size-xs)",
              fontWeight:
                geoMode === "radius"
                  ? "var(--font-weight-semibold)"
                  : "var(--font-weight-normal)",
              background: geoMode === "radius" ? "var(--color-surface)" : "transparent",
              color:
                geoMode === "radius"
                  ? "var(--color-primary)"
                  : "var(--color-text-muted)",
              border: "none",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              boxShadow: geoMode === "radius" ? "var(--shadow-xs)" : "none",
            }}
            aria-pressed={geoMode === "radius"}
          >
            🎯 Hyperlocal Radius
          </button>
        </div>
      </div>

      {panelError && (
        <Alert variant="error" style={{ marginBottom: "var(--space-3)" }}>
          {panelError}
        </Alert>
      )}

      {/* Mode 1: Administrative Regions */}
      {geoMode === "administrative" && (
        <div
          style={{
            background: "var(--color-bg-subtle)",
            padding: "var(--space-3)",
            borderRadius: "var(--radius-md)",
            marginBottom: "var(--space-4)",
          }}
        >
          {/* Editing State Banner for Administrative Selection */}
          {editingTarget && !isRadiusGeoEntry(editingTarget.entry) && (
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "var(--space-3)",
                padding: "var(--space-2) var(--space-3)",
                background: "rgba(245, 158, 11, 0.1)",
                borderRadius: "var(--radius-md)",
                fontSize: "var(--font-size-xs)",
                color: "var(--color-warning-text, #b45309)",
              }}
            >
              <span>
                ✏️ <strong>Editing {editingTarget.mode === "include" ? "Included" : "Excluded"} Location:</strong>{" "}
                {formatGeoEntry(editingTarget.entry)}
              </span>
              <button
                type="button"
                onClick={handleCancelEdit}
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
            </div>
          )}

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "var(--space-2)",
              marginBottom: "var(--space-2)",
            }}
          >
            <Select
              label="Country"
              value={selCountry}
              onChange={(e) => setSelCountry(e.target.value)}
              disabled={disabled}
              options={[
                { value: "", label: "-- Select Country --" },
                ...countries.map((c) => ({ value: c, label: c })),
              ]}
            />
            <Select
              label="State"
              value={selState}
              onChange={(e) => setSelState(e.target.value)}
              disabled={disabled || !selCountry || states.length === 0}
              options={[
                { value: "", label: "-- Select State --" },
                ...states.map((s) => ({ value: s.code, label: s.name })),
              ]}
            />
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "var(--space-2)",
              marginBottom: "var(--space-3)",
            }}
          >
            <Select
              label="City"
              value={selCity}
              onChange={(e) => setSelCity(e.target.value)}
              disabled={disabled || !selState || cities.length === 0}
              options={[
                { value: "", label: "-- Select City --" },
                ...cities.map((ct) => ({ value: ct, label: ct })),
              ]}
            />
            <Select
              label="Pincode"
              value={selPincode}
              onChange={(e) => setSelPincode(e.target.value)}
              disabled={disabled || !selCity || pincodes.length === 0}
              options={[
                { value: "", label: "-- Select Pincode --" },
                ...pincodes.map((p) => ({ value: p, label: p })),
              ]}
            />
          </div>

          {editingTarget && !isRadiusGeoEntry(editingTarget.entry) ? (
            <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", alignItems: "center" }}>
              <Button
                variant="primary"
                size="sm"
                type="button"
                disabled={disabled || !selCountry}
                onClick={() => handleUpdateAdminGeo(editingTarget.mode)}
              >
                ✓ Update {editingTarget.mode === "include" ? "Included" : "Excluded"} Location
              </Button>
              <Button
                variant="outline"
                size="sm"
                type="button"
                disabled={disabled || !selCountry}
                onClick={() => handleUpdateAdminGeo(editingTarget.mode === "include" ? "exclude" : "include")}
              >
                Switch to {editingTarget.mode === "include" ? "Exclude" : "Include"} & Update
              </Button>
              <Button
                variant="ghost"
                size="sm"
                type="button"
                disabled={disabled}
                onClick={handleCancelEdit}
              >
                Cancel
              </Button>
            </div>
          ) : (
            <div style={{ display: "flex", gap: "var(--space-2)" }}>
              <Button
                variant="outline"
                size="sm"
                type="button"
                disabled={disabled || !selCountry}
                onClick={() => handleAddAdminGeo("include")}
              >
                + Include Location
              </Button>
              <Button
                variant="outline"
                size="sm"
                type="button"
                disabled={disabled || !selCountry}
                onClick={() => handleAddAdminGeo("exclude")}
              >
                - Exclude Location
              </Button>
            </div>
          )}
        </div>
      )}

      {/* Mode 2: Hyperlocal Radius Map & Coordinate Picker */}
      {geoMode === "radius" && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <RadiusMapPicker
            onAddRadiusTarget={handleAddRadiusGeo}
            editingTarget={
              editingTarget && isRadiusGeoEntry(editingTarget.entry)
                ? {
                    entry: {
                      latitude: editingTarget.entry.latitude as number,
                      longitude: editingTarget.entry.longitude as number,
                      radius_meters: editingTarget.entry.radius_meters as number,
                      radius_unit: editingTarget.entry.radius_unit as RadiusUnit | undefined,
                      name: editingTarget.entry.name,
                      country: editingTarget.entry.country,
                    },
                    mode: editingTarget.mode,
                  }
                : null
            }
            onUpdateRadiusTarget={handleUpdateRadiusGeo}
            onCancelEdit={handleCancelEdit}
            disabled={disabled}
          />
        </div>
      )}

      {/* Shared Target Summaries: Included & Excluded Locations */}
      <div style={{ marginBottom: "var(--space-3)" }}>
        <div
          style={{
            fontSize: "var(--font-size-xs)",
            fontWeight: "var(--font-weight-medium)",
            color: "var(--color-success-text)",
            marginBottom: "var(--space-1)",
          }}
        >
          Included Locations ({geoInclude.length})
        </div>
        <div className="chips-wrap">
          {geoInclude.length === 0 && (
            <span className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
              Unrestricted (Global Serving)
            </span>
          )}
          {geoInclude.map((geo, idx) => (
            <span
              key={`inc-${idx}`}
              className="chip chip-include"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "var(--space-1)",
                border:
                  editingTarget?.mode === "include" && editingTarget?.index === idx
                    ? "2px solid var(--color-primary)"
                    : undefined,
              }}
            >
              {formatGeoEntry(geo)}
              <button
                type="button"
                className="chip-edit"
                onClick={() => handleStartEdit("include", idx)}
                aria-label={`Edit included location ${formatGeoEntry(geo)}`}
                disabled={disabled}
                style={{
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  padding: "0 2px",
                  fontSize: "12px",
                  color: "inherit",
                  opacity: 0.8,
                }}
                title="Edit location"
              >
                ✎
              </button>
              <button
                type="button"
                className="chip-remove"
                onClick={() => handleRemoveGeo("include", idx)}
                aria-label={`Remove included location ${formatGeoEntry(geo)}`}
                disabled={disabled}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      </div>

      <div>
        <div
          style={{
            fontSize: "var(--font-size-xs)",
            fontWeight: "var(--font-weight-medium)",
            color: "var(--color-error-text)",
            marginBottom: "var(--space-1)",
          }}
        >
          Excluded Locations ({geoExclude.length})
        </div>
        <div className="chips-wrap">
          {geoExclude.length === 0 && (
            <span className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
              None
            </span>
          )}
          {geoExclude.map((geo, idx) => (
            <span
              key={`exc-${idx}`}
              className="chip chip-exclude"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "var(--space-1)",
                border:
                  editingTarget?.mode === "exclude" && editingTarget?.index === idx
                    ? "2px solid var(--color-primary)"
                    : undefined,
              }}
            >
              {formatGeoEntry(geo)}
              <button
                type="button"
                className="chip-edit"
                onClick={() => handleStartEdit("exclude", idx)}
                aria-label={`Edit excluded location ${formatGeoEntry(geo)}`}
                disabled={disabled}
                style={{
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  padding: "0 2px",
                  fontSize: "12px",
                  color: "inherit",
                  opacity: 0.8,
                }}
                title="Edit location"
              >
                ✎
              </button>
              <button
                type="button"
                className="chip-remove"
                onClick={() => handleRemoveGeo("exclude", idx)}
                aria-label={`Remove excluded location ${formatGeoEntry(geo)}`}
                disabled={disabled}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
