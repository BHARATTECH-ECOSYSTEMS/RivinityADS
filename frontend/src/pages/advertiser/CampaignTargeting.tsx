import { useEffect, useState, type FormEvent } from "react";
import {
  type GeoEntry,
  type TargetingSet,
  getCampaignTargeting,
  updateCampaignTargeting,
  getAdSetTargeting,
  updateAdSetTargeting,
  getReferenceSectors,
  formatGeoEntry,
} from "../../api/client";
import type { AdSet } from "../../types";
import { Card, Button, Select, Alert, LoadingSkeleton } from "../../components/ui";
import { TargetingGeoPanel } from "../../components/targeting/TargetingGeoPanel";

interface CampaignTargetingProps {
  campaignId: number;
  adSets?: AdSet[];
}

export default function CampaignTargeting({ campaignId, adSets = [] }: CampaignTargetingProps) {
  const [selectedTargetScope, setSelectedTargetScope] = useState<"campaign" | number>(
    adSets.length > 0 ? adSets[0].id : "campaign"
  );

  const [targeting, setTargeting] = useState<TargetingSet | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // Sector Reference Data State
  const [sectors, setSectors] = useState<string[]>([]);
  const [selSector, setSelSector] = useState("");

  const loadTargeting = async () => {
    setLoading(true);
    setError(null);
    setSuccessMessage(null);
    try {
      if (typeof selectedTargetScope === "number") {
        const data = await getAdSetTargeting(selectedTargetScope);
        setTargeting({
          geo_include: data.geo_include || [],
          geo_exclude: data.geo_exclude || [],
          sector_include: data.sector_include || [],
          sector_exclude: data.sector_exclude || [],
        });
      } else {
        const data = await getCampaignTargeting(campaignId);
        setTargeting({
          geo_include: data.geo_include || [],
          geo_exclude: data.geo_exclude || [],
          sector_include: data.sector_include || [],
          sector_exclude: data.sector_exclude || [],
        });
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load targeting configuration");
    } finally {
      setLoading(false);
    }
  };

  const loadReferences = async () => {
    try {
      const sData = await getReferenceSectors();
      setSectors(sData);
    } catch {
      // Non-critical reference load failure
    }
  };

  useEffect(() => {
    void loadTargeting();
  }, [campaignId, selectedTargetScope]);

  useEffect(() => {
    void loadReferences();
  }, []);

  const addSector = (type: "include" | "exclude") => {
    if (!selSector || !targeting) return;
    const key = type === "include" ? "sector_include" : "sector_exclude";
    if (!targeting[key].includes(selSector)) {
      setTargeting({ ...targeting, [key]: [...targeting[key], selSector] });
    }
    setSelSector("");
  };

  const removeSector = (type: "include" | "exclude", sector: string) => {
    if (!targeting) return;
    const key = type === "include" ? "sector_include" : "sector_exclude";
    setTargeting({ ...targeting, [key]: targeting[key].filter((s) => s !== sector) });
  };

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    if (!targeting || saving) return;
    setSaving(true);
    setError(null);
    setSuccessMessage(null);
    try {
      if (typeof selectedTargetScope === "number") {
        await updateAdSetTargeting(selectedTargetScope, targeting);
        setSuccessMessage("Ad Set targeting updated successfully!");
      } else {
        await updateCampaignTargeting(campaignId, targeting);
        setSuccessMessage("Campaign targeting updated successfully!");
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save targeting settings");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card style={{ marginBottom: "var(--space-6)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-4)" }}>
        <div>
          <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-1) 0" }}>
            Audience & Delivery Targeting
          </h3>
          <p className="text-muted" style={{ fontSize: "var(--font-size-xs)", margin: 0 }}>
            Configure granular geographic (administrative & hyperlocal radius) and contextual sector inclusions and exclusions.
          </p>
        </div>

        {adSets.length > 0 && (
          <div style={{ minWidth: "220px" }}>
            <Select
              label="Targeting Scope"
              value={String(selectedTargetScope)}
              onChange={(e) => {
                const val = e.target.value;
                setSelectedTargetScope(val === "campaign" ? "campaign" : Number(val));
              }}
              options={[
                ...adSets.map((a) => ({ value: String(a.id), label: `Ad Set: ${a.name}` })),
                { value: "campaign", label: "Campaign Level (Legacy)" },
              ]}
            />
          </div>
        )}
      </div>

      {error && (
        <Alert variant="error" style={{ marginBottom: "var(--space-4)" }}>
          {error}
        </Alert>
      )}

      {successMessage && (
        <Alert variant="success" style={{ marginBottom: "var(--space-4)" }}>
          {successMessage}
        </Alert>
      )}

      {loading && !targeting ? (
        <LoadingSkeleton variant="rect" height={220} />
      ) : targeting ? (
        <form onSubmit={handleSave}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-6)", marginBottom: "var(--space-4)" }}>
            {/* Geographic Targeting Panel (Administrative & Hyperlocal Radius) */}
            <TargetingGeoPanel
              geoInclude={targeting.geo_include}
              geoExclude={targeting.geo_exclude}
              onChange={(geo_include, geo_exclude) =>
                setTargeting({ ...targeting, geo_include, geo_exclude })
              }
              disabled={saving}
            />

            {/* Sector / Contextual Targeting Panel */}
            <div style={{ border: "1px solid var(--color-border)", borderRadius: "var(--radius-lg)", padding: "var(--space-4)" }}>
              <h4 style={{ fontSize: "var(--font-size-sm)", fontWeight: "var(--font-weight-bold)", margin: "0 0 var(--space-3) 0" }}>
                🎯 Sector & Contextual Matching
              </h4>

              <div style={{ marginBottom: "var(--space-3)" }}>
                <Select
                  label="Available Industry Sectors"
                  value={selSector}
                  onChange={(e) => setSelSector(e.target.value)}
                  options={[{ value: "", label: "-- Choose a Sector --" }, ...sectors.map((s) => ({ value: s, label: s }))]}
                />
              </div>

              <div style={{ display: "flex", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
                <Button variant="outline" size="sm" type="button" disabled={!selSector} onClick={() => addSector("include")}>
                  + Include Sector
                </Button>
                <Button variant="outline" size="sm" type="button" disabled={!selSector} onClick={() => addSector("exclude")}>
                  - Exclude Sector
                </Button>
              </div>

              <div style={{ marginBottom: "var(--space-3)" }}>
                <div style={{ fontSize: "var(--font-size-xs)", fontWeight: "var(--font-weight-medium)", color: "var(--color-success-text)" }}>
                  Included Sectors ({targeting.sector_include.length})
                </div>
                <div className="chips-wrap">
                  {targeting.sector_include.length === 0 && (
                    <span className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
                      Unrestricted (All publisher contexts)
                    </span>
                  )}
                  {targeting.sector_include.map((sec) => (
                    <span key={sec} className="chip chip-include">
                      {sec}
                      <button type="button" className="chip-remove" onClick={() => removeSector("include", sec)}>
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              </div>

              <div>
                <div style={{ fontSize: "var(--font-size-xs)", fontWeight: "var(--font-weight-medium)", color: "var(--color-error-text)" }}>
                  Excluded Sectors ({targeting.sector_exclude.length})
                </div>
                <div className="chips-wrap">
                  {targeting.sector_exclude.length === 0 && (
                    <span className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>None</span>
                  )}
                  {targeting.sector_exclude.map((sec) => (
                    <span key={sec} className="chip chip-exclude">
                      {sec}
                      <button type="button" className="chip-remove" onClick={() => removeSector("exclude", sec)}>
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <Button type="submit" variant="primary" isLoading={saving} disabled={saving}>
              Save Targeting Configuration
            </Button>
          </div>
        </form>
      ) : null}
    </Card>
  );
}
