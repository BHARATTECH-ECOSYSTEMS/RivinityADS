import { useState, type FormEvent } from "react";
import { createCampaign, updateCampaign } from "../../../api/client";
import { useAuth } from "../../../auth/AuthContext";
import { BID_TYPES, type Campaign } from "../../../types";
import { Button, Input, Select, Alert, Card } from "../../../components/ui";
import type { CampaignFormData } from "./types";

interface CampaignStepProps {
  campaign: Campaign | null;
  onSaved: (campaign: Campaign) => void;
  onContinue: () => void;
}

export function CampaignStep({ campaign, onSaved, onContinue }: CampaignStepProps) {
  const { session } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [form, setForm] = useState<CampaignFormData>(() => ({
    name: campaign?.name || "",
    description: campaign?.description || "",
    budget_daily: campaign ? String(campaign.budget_daily) : "100",
    budget_total: campaign ? String(campaign.budget_total) : "1000",
    bid_type: campaign ? String(campaign.bid_type) : "1",
    bid_amount: campaign ? String(campaign.bid_amount) : "5",
    freq_cap_daily: campaign ? String(campaign.freq_cap_daily) : "10",
    freq_cap_hourly: campaign ? String(campaign.freq_cap_hourly) : "3",
  }));

  const updateField = (key: keyof CampaignFormData, val: string) => {
    setForm((prev) => ({ ...prev, [key]: val }));
    if (error) setError(null);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (loading) return;

    if (!session?.user.advertiser_id) {
      setError("Your account is not linked to an active advertiser account.");
      return;
    }

    const daily = Number(form.budget_daily);
    const total = Number(form.budget_total);
    const bidAmount = Number(form.bid_amount);

    if (!form.name.trim()) {
      setError("Campaign name is required.");
      return;
    }
    if (isNaN(daily) || daily <= 0) {
      setError("Daily budget must be greater than zero.");
      return;
    }
    if (isNaN(total) || total < daily) {
      setError("Total budget cannot be less than daily budget.");
      return;
    }
    if (isNaN(bidAmount) || bidAmount <= 0) {
      setError("Bid amount must be greater than zero.");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      let savedCampaign: Campaign;
      if (campaign?.id) {
        savedCampaign = await updateCampaign(campaign.id, {
          name: form.name.trim(),
          description: form.description.trim() || undefined,
          budget_daily: daily,
          budget_total: total,
          bid_type: Number(form.bid_type),
          bid_amount: bidAmount,
          freq_cap_daily: Number(form.freq_cap_daily) || 10,
          freq_cap_hourly: Number(form.freq_cap_hourly) || 3,
        });
      } else {
        savedCampaign = await createCampaign({
          advertiser_id: session.user.advertiser_id,
          name: form.name.trim(),
          description: form.description.trim() || undefined,
          budget_daily: daily,
          budget_total: total,
          bid_type: Number(form.bid_type),
          bid_amount: bidAmount,
          freq_cap_daily: Number(form.freq_cap_daily) || 10,
          freq_cap_hourly: Number(form.freq_cap_hourly) || 3,
        });
      }

      onSaved(savedCampaign);
      onContinue();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save campaign");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <div style={{ marginBottom: "var(--space-4)" }}>
        <h2 style={{ fontSize: "var(--font-size-xl)", fontWeight: "var(--font-weight-bold)", margin: "0 0 var(--space-2) 0" }}>
          Step 1: Campaign Setup
        </h2>
        <p className="text-muted" style={{ margin: 0 }}>
          Define high-level campaign parameters, lifetime and daily spend ceilings, and bidding strategy.
        </p>
      </div>

      <Alert
        variant="info"
        title="Authoritative Budget Safeguards"
        style={{ marginBottom: "var(--space-6)" }}
      >
        Campaign-level daily and total budgets represent hard ceilings enforced across all Ad Sets.
        Individual Ad Set budgets are sub-limits that draw from this master campaign budget.
      </Alert>

      {error && (
        <Alert variant="error" style={{ marginBottom: "var(--space-4)" }}>
          {error}
        </Alert>
      )}

      <form onSubmit={handleSubmit}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "var(--space-4)", marginBottom: "var(--space-4)" }}>
          <Input
            label="Campaign Name"
            placeholder="e.g. Q4 Growth Promo"
            value={form.name}
            onChange={(e) => updateField("name", e.target.value)}
            required
            disabled={loading}
          />
          <Input
            label="Description (Optional)"
            placeholder="Brief internal note"
            value={form.description}
            onChange={(e) => updateField("description", e.target.value)}
            disabled={loading}
          />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "var(--space-4)", marginBottom: "var(--space-4)" }}>
          <Input
            label="Daily Hard Budget ($)"
            type="number"
            min="1"
            step="0.01"
            value={form.budget_daily}
            onChange={(e) => updateField("budget_daily", e.target.value)}
            required
            hint="Maximum allowed spend per calendar day"
            disabled={loading}
          />
          <Input
            label="Total Lifetime Budget ($)"
            type="number"
            min="1"
            step="0.01"
            value={form.budget_total}
            onChange={(e) => updateField("budget_total", e.target.value)}
            required
            hint="Must be ≥ Daily Budget"
            disabled={loading}
          />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "var(--space-4)", marginBottom: "var(--space-4)" }}>
          <Select
            label="Default Bid Type"
            value={form.bid_type}
            onChange={(e) => updateField("bid_type", e.target.value)}
            options={Object.entries(BID_TYPES).map(([val, label]) => ({
              value: val,
              label: `${label} (Pricing Model)`,
            }))}
            disabled={loading}
          />
          <Input
            label="Default Bid Amount ($)"
            type="number"
            min="0.01"
            step="0.01"
            value={form.bid_amount}
            onChange={(e) => updateField("bid_amount", e.target.value)}
            required
            hint="Base bid for eligible auctions"
            disabled={loading}
          />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "var(--space-4)", marginBottom: "var(--space-6)" }}>
          <Input
            label="Daily Frequency Cap"
            type="number"
            min="1"
            value={form.freq_cap_daily}
            onChange={(e) => updateField("freq_cap_daily", e.target.value)}
            hint="Max impressions per unique user / day"
            disabled={loading}
          />
          <Input
            label="Hourly Frequency Cap"
            type="number"
            min="1"
            value={form.freq_cap_hourly}
            onChange={(e) => updateField("freq_cap_hourly", e.target.value)}
            hint="Max impressions per unique user / hour"
            disabled={loading}
          />
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-3)" }}>
          <Button
            type="submit"
            variant="primary"
            isLoading={loading}
            disabled={loading}
          >
            {campaign ? "Update & Continue to Ad Sets →" : "Create Campaign & Continue →"}
          </Button>
        </div>
      </form>
    </Card>
  );
}
