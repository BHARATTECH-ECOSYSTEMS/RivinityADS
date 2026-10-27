import { useEffect, useState, type FormEvent } from "react";
import {
  createAdSet,
  updateAdSet,
  listAdSets,
  getAdSetTargeting,
  updateAdSetTargeting,
  getReferenceSectors,
  formatGeoEntry,
  type GeoEntry,
  type TargetingSet,
} from "../../../api/client";
import { BID_TYPES, type Campaign, type AdSet } from "../../../types";
import { Button, Input, Select, Alert, Card, StatusBadge } from "../../../components/ui";
import { TargetingGeoPanel } from "../../../components/targeting/TargetingGeoPanel";

interface AdSetsStepProps {
  campaign: Campaign;
  adSets: AdSet[];
  selectedAdSetId: number | null;
  onAdSetsUpdated: (adSets: AdSet[], selectedId?: number) => void;
  onBack: () => void;
  onContinue: () => void;
}

export function AdSetsStep({
  campaign,
  adSets,
  selectedAdSetId,
  onAdSetsUpdated,
  onBack,
  onContinue,
}: AdSetsStepProps) {
  const [activeAdSetId, setActiveAdSetId] = useState<number | null>(
    selectedAdSetId || (adSets.length > 0 ? adSets[0].id : null)
  );

  const [loading, setLoading] = useState(false);
  const [targetingLoading, setTargetingLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [targetingSuccess, setTargetingSuccess] = useState<string | null>(null);

  // Form for New/Editing Ad Set
  const [isEditing, setIsEditing] = useState(false);
  const [name, setName] = useState("");
  const [dailyBudget, setDailyBudget] = useState(String(campaign.budget_daily));
  const [bidType, setBidType] = useState(String(campaign.bid_type));
  const [bidAmount, setBidAmount] = useState(String(campaign.bid_amount));
  const [deliveryMode, setDeliveryMode] = useState("standard");
  const [freqCapDaily, setFreqCapDaily] = useState(String(campaign.freq_cap_daily || 10));
  const [freqCapHourly, setFreqCapHourly] = useState(String(campaign.freq_cap_hourly || 3));

  // Targeting state for active Ad Set
  const [targeting, setTargeting] = useState<TargetingSet>({
    geo_include: [],
    geo_exclude: [],
    sector_include: [],
    sector_exclude: [],
  });

  // Sector reference data
  const [sectors, setSectors] = useState<string[]>([]);
  const [selSector, setSelSector] = useState("");

  // Load sector reference data
  useEffect(() => {
    void getReferenceSectors().then(setSectors).catch(() => {});
  }, []);

  // Synchronize active Ad Set selection
  useEffect(() => {
    if (!activeAdSetId && adSets.length > 0) {
      setActiveAdSetId(adSets[0].id);
    }
  }, [adSets, activeAdSetId]);

  // Load targeting for currently active Ad Set
  useEffect(() => {
    if (!activeAdSetId) {
      setTargeting({ geo_include: [], geo_exclude: [], sector_include: [], sector_exclude: [] });
      return;
    }

    let isMounted = true;
    setTargetingLoading(true);
    setTargetingSuccess(null);

    getAdSetTargeting(activeAdSetId)
      .then((res) => {
        if (!isMounted) return;
        setTargeting({
          geo_include: res.geo_include || [],
          geo_exclude: res.geo_exclude || [],
          sector_include: res.sector_include || [],
          sector_exclude: res.sector_exclude || [],
        });
      })
      .catch(() => {
        if (!isMounted) return;
        setTargeting({ geo_include: [], geo_exclude: [], sector_include: [], sector_exclude: [] });
      })
      .finally(() => {
        if (isMounted) setTargetingLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, [activeAdSetId]);

  const activeAdSet = adSets.find((a) => a.id === activeAdSetId);

  // Populate form when editing
  const handleEditClick = (adSet: AdSet) => {
    setActiveAdSetId(adSet.id);
    setIsEditing(true);
    setName(adSet.name);
    setDailyBudget(adSet.daily_budget != null ? String(adSet.daily_budget) : String(campaign.budget_daily));
    setBidType(String(adSet.bid_type));
    setBidAmount(String(adSet.bid_amount));
    setDeliveryMode(adSet.delivery_mode || "standard");
    setFreqCapDaily(adSet.freq_cap_daily != null ? String(adSet.freq_cap_daily) : "10");
    setFreqCapHourly(adSet.freq_cap_hourly != null ? String(adSet.freq_cap_hourly) : "3");
  };

  const handleStartNewAdSet = () => {
    setIsEditing(true);
    setName(`Ad Set ${adSets.length + 1}`);
    setDailyBudget(String(campaign.budget_daily));
    setBidType(String(campaign.bid_type));
    setBidAmount(String(campaign.bid_amount));
    setDeliveryMode("standard");
    setFreqCapDaily("10");
    setFreqCapHourly("3");
    setActiveAdSetId(null);
  };

  const handleSaveAdSet = async (e: FormEvent) => {
    e.preventDefault();
    if (loading) return;

    if (!name.trim()) {
      setError("Ad Set name is required.");
      return;
    }

    const budgetNum = Number(dailyBudget);
    if (!isNaN(budgetNum) && budgetNum > campaign.budget_daily) {
      setError(
        `Ad Set daily budget ($${budgetNum}) cannot exceed the campaign daily budget ($${campaign.budget_daily}).`
      );
      return;
    }

    const bidNum = Number(bidAmount);
    if (isNaN(bidNum) || bidNum <= 0) {
      setError("Bid amount must be greater than zero.");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      if (activeAdSetId && isEditing) {
        // Update existing AdSet
        const updated = await updateAdSet(activeAdSetId, {
          name: name.trim(),
          daily_budget: isNaN(budgetNum) ? null : budgetNum,
          bid_type: Number(bidType),
          bid_amount: bidNum,
          delivery_mode: deliveryMode,
          freq_cap_daily: Number(freqCapDaily) || null,
          freq_cap_hourly: Number(freqCapHourly) || null,
        });
        const nextList = adSets.map((a) => (a.id === updated.id ? updated : a));
        onAdSetsUpdated(nextList, updated.id);
        setIsEditing(false);
      } else {
        // Create new AdSet
        const created = await createAdSet(campaign.id, {
          name: name.trim(),
          daily_budget: isNaN(budgetNum) ? null : budgetNum,
          bid_type: Number(bidType),
          bid_amount: bidNum,
          delivery_mode: deliveryMode,
          freq_cap_daily: Number(freqCapDaily) || null,
          freq_cap_hourly: Number(freqCapHourly) || null,
        });
        const nextList = [...adSets, created];
        setActiveAdSetId(created.id);
        onAdSetsUpdated(nextList, created.id);
        setIsEditing(false);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save Ad Set");
    } finally {
      setLoading(false);
    }
  };

  // Targeting handlers
  const handleAddSector = (type: "include" | "exclude") => {
    if (!selSector) return;
    const key = type === "include" ? "sector_include" : "sector_exclude";
    if (!targeting[key].includes(selSector)) {
      setTargeting((prev) => ({ ...prev, [key]: [...prev[key], selSector] }));
    }
    setSelSector("");
  };

  const handleRemoveSector = (type: "include" | "exclude", sec: string) => {
    const key = type === "include" ? "sector_include" : "sector_exclude";
    setTargeting((prev) => ({
      ...prev,
      [key]: prev[key].filter((s) => s !== sec),
    }));
  };

  const handleSaveTargeting = async () => {
    if (!activeAdSetId) return;
    setLoading(true);
    setError(null);
    setTargetingSuccess(null);
    try {
      await updateAdSetTargeting(activeAdSetId, targeting);
      setTargetingSuccess("Ad Set targeting saved successfully!");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update Ad Set targeting");
    } finally {
      setLoading(false);
    }
  };

  const handleContinueWithAutoSave = async () => {
    if (activeAdSetId) {
      setLoading(true);
      setError(null);
      try {
        await updateAdSetTargeting(activeAdSetId, targeting);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to save Ad Set targeting");
        setLoading(false);
        return;
      } finally {
        setLoading(false);
      }
    }
    onContinue();
  };

  const formatGeoLabel = (e: GeoEntry) => formatGeoEntry(e);

  return (
    <div>
      <Card style={{ marginBottom: "var(--space-6)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-4)" }}>
          <div>
            <h2 style={{ fontSize: "var(--font-size-xl)", fontWeight: "var(--font-weight-bold)", margin: "0 0 var(--space-1) 0" }}>
              Step 2: Ad Sets & Targeting
            </h2>
            <p className="text-muted" style={{ margin: 0 }}>
              Ad Sets group your ads, schedule, sub-budgets, and specific audience targeting rules.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={handleStartNewAdSet}>
            + Add Another Ad Set
          </Button>
        </div>

        {error && (
          <Alert variant="error" style={{ marginBottom: "var(--space-4)" }}>
            {error}
          </Alert>
        )}

        {/* Sibling Ad Sets Tabs / Selection */}
        {adSets.length === 0 ? (
          <Alert variant="warning" style={{ marginBottom: "var(--space-4)" }}>
            No Ad Sets created yet. Complete the form below to create the initial Ad Set for this campaign.
          </Alert>
        ) : (
          <div style={{ display: "flex", gap: "var(--space-2)", overflowX: "auto", marginBottom: "var(--space-4)", paddingBottom: "var(--space-2)" }}>
            {adSets.map((adSet) => {
              const isSelected = adSet.id === activeAdSetId;
              return (
                <div
                  key={adSet.id}
                  onClick={() => {
                    setActiveAdSetId(adSet.id);
                    setIsEditing(false);
                  }}
                  style={{
                    padding: "var(--space-3) var(--space-4)",
                    borderRadius: "var(--radius-lg)",
                    border: `2px solid ${isSelected ? "var(--color-primary)" : "var(--color-border)"}`,
                    background: isSelected ? "var(--color-bg-subtle)" : "var(--color-bg-surface)",
                    cursor: "pointer",
                    minWidth: "200px",
                    transition: "all var(--transition-fast)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-1)" }}>
                    <strong style={{ fontSize: "var(--font-size-sm)" }}>{adSet.name}</strong>
                    <StatusBadge status={adSet.status} />
                  </div>
                  <div className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
                    Daily: ${adSet.daily_budget != null ? adSet.daily_budget : campaign.budget_daily} | Bid: ${adSet.bid_amount}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Ad Set Details Form (Create / Edit) */}
        {(isEditing || adSets.length === 0) && (
          <div style={{ background: "var(--color-bg-subtle)", padding: "var(--space-4)", borderRadius: "var(--radius-lg)", marginBottom: "var(--space-6)" }}>
            <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-3) 0" }}>
              {activeAdSetId ? "Edit Ad Set Configuration" : "Configure New Ad Set"}
            </h3>
            <form onSubmit={handleSaveAdSet}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "var(--space-3)", marginBottom: "var(--space-3)" }}>
                <Input
                  label="Ad Set Name"
                  placeholder="e.g. Metro Developers"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
                <Input
                  label="Daily Sub-Budget ($)"
                  type="number"
                  min="1"
                  step="0.01"
                  value={dailyBudget}
                  onChange={(e) => setDailyBudget(e.target.value)}
                  hint={`Max $${campaign.budget_daily} (Campaign Daily)`}
                />
                <Select
                  label="Bid Type"
                  value={bidType}
                  onChange={(e) => setBidType(e.target.value)}
                  options={Object.entries(BID_TYPES).map(([v, l]) => ({ value: v, label: l }))}
                />
                <Input
                  label="Bid Amount ($)"
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={bidAmount}
                  onChange={(e) => setBidAmount(e.target.value)}
                  required
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "var(--space-3)", marginBottom: "var(--space-4)" }}>
                <Select
                  label="Delivery Mode"
                  value={deliveryMode}
                  onChange={(e) => setDeliveryMode(e.target.value)}
                  options={[
                    { value: "standard", label: "Standard (Even pacing)" },
                    { value: "accelerated", label: "Accelerated (Fastest delivery)" },
                  ]}
                />
                <Input
                  label="Daily Frequency Cap"
                  type="number"
                  min="1"
                  value={freqCapDaily}
                  onChange={(e) => setFreqCapDaily(e.target.value)}
                />
                <Input
                  label="Hourly Frequency Cap"
                  type="number"
                  min="1"
                  value={freqCapHourly}
                  onChange={(e) => setFreqCapHourly(e.target.value)}
                />
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)" }}>
                {activeAdSetId && (
                  <Button variant="ghost" size="sm" onClick={() => setIsEditing(false)}>
                    Cancel
                  </Button>
                )}
                <Button type="submit" variant="primary" size="sm" isLoading={loading}>
                  {activeAdSetId ? "Save Ad Set Details" : "Create Ad Set"}
                </Button>
              </div>
            </form>
          </div>
        )}

        {/* Selected Ad Set Targeting Controls */}
        {activeAdSet && !isEditing && (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-3)" }}>
              <div>
                <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-1) 0" }}>
                  Targeting Configuration for: <em>{activeAdSet.name}</em>
                </h3>
                <p className="text-muted" style={{ fontSize: "var(--font-size-xs)", margin: 0 }}>
                  Targeting rules are strictly isolated to this Ad Set and do not leak to sibling Ad Sets.
                </p>
              </div>
              <Button variant="outline" size="sm" onClick={() => handleEditClick(activeAdSet)}>
                Edit Settings
              </Button>
            </div>

            {targetingSuccess && (
              <Alert variant="success" style={{ marginBottom: "var(--space-4)" }}>
                {targetingSuccess}
              </Alert>
            )}

            {targetingLoading ? (
              <div className="text-muted" style={{ padding: "var(--space-4)", textAlign: "center" }}>
                Loading Ad Set targeting...
              </div>
            ) : (
              <>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-6)", marginBottom: "var(--space-4)" }}>
                  {/* Geographic Targeting Panel (Administrative & Hyperlocal Radius) */}
                  <TargetingGeoPanel
                    geoInclude={targeting.geo_include}
                    geoExclude={targeting.geo_exclude}
                    onChange={(geo_include, geo_exclude) =>
                      setTargeting((prev) => ({ ...prev, geo_include, geo_exclude }))
                    }
                    disabled={loading}
                  />

                  {/* Sector / Contextual Targeting Panel */}
                  <div style={{ border: "1px solid var(--color-border)", borderRadius: "var(--radius-lg)", padding: "var(--space-4)" }}>
                    <h4 style={{ fontSize: "var(--font-size-sm)", fontWeight: "var(--font-weight-bold)", margin: "0 0 var(--space-3) 0" }}>
                      🎯 Sector & Category Targeting
                    </h4>

                    <div style={{ marginBottom: "var(--space-3)" }}>
                      <Select
                        label="Available Sectors"
                        value={selSector}
                        onChange={(e) => setSelSector(e.target.value)}
                        options={[{ value: "", label: "-- Choose a Sector --" }, ...sectors.map((s) => ({ value: s, label: s }))]}
                      />
                    </div>

                    <div style={{ display: "flex", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={!selSector}
                        onClick={() => handleAddSector("include")}
                      >
                        + Include Sector
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={!selSector}
                        onClick={() => handleAddSector("exclude")}
                      >
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
                            No sector restrictions (Delivers across all sectors)
                          </span>
                        )}
                        {targeting.sector_include.map((sec) => (
                          <span key={sec} className="chip chip-include">
                            {sec}
                            <button
                              type="button"
                              className="chip-remove"
                              onClick={() => handleRemoveSector("include", sec)}
                              aria-label={`Remove included sector ${sec}`}
                            >
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
                            <button
                              type="button"
                              className="chip-remove"
                              onClick={() => handleRemoveSector("exclude", sec)}
                              aria-label={`Remove excluded sector ${sec}`}
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Dedicated Ad Set Targeting Save Action Bar */}
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    background: "var(--color-bg-subtle)",
                    padding: "var(--space-3) var(--space-4)",
                    borderRadius: "var(--radius-lg)",
                    border: "1px solid var(--color-border)",
                    marginBottom: "var(--space-4)",
                  }}
                >
                  <div style={{ fontSize: "var(--font-size-sm)", color: "var(--color-text-muted)" }}>
                    💾 Save Geographic & Sector targeting rules specifically for this Ad Set.
                  </div>
                  <Button variant="primary" size="sm" onClick={handleSaveTargeting} isLoading={loading}>
                    Save Ad Set Targeting
                  </Button>
                </div>
              </>
            )}
          </div>
        )}

        {/* Wizard Footer Controls */}
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: "var(--space-6)", paddingTop: "var(--space-4)", borderTop: "1px solid var(--color-border)" }}>
          <Button variant="outline" onClick={onBack}>
            ← Back to Campaign
          </Button>
          <Button
            variant="primary"
            onClick={handleContinueWithAutoSave}
            disabled={adSets.length === 0}
            isLoading={loading}
          >
            Continue to Ads (Creatives) →
          </Button>
        </div>
      </Card>
    </div>
  );
}
