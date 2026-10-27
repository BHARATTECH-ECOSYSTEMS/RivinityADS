import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import {
  getCampaign,
  updateCampaign,
  activateCampaign,
  listAdSets,
  createAdSet,
  pauseAdSet,
  resumeAdSet,
  listCreatives,
  createCreative,
  getAdvertiserReporting,
  getCampaignSuggestions,
  updateSuggestionStatus,
  listAdvertiserDomains,
  type AdvertiserReporting,
  type AdvertiserDomain,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import {
  PageContainer,
  PageHeader,
  Card,
  Button,
  StatusBadge,
  Alert,
  LoadingSkeleton,
  Input,
  Select,
} from "../../components/ui";
import StatCard from "../../components/StatCard";
import DateRangeSelector, { type DateRangeState } from "../../components/DateRangeSelector";
import TimeSeriesChart from "../../components/TimeSeriesChart";
import { ctr, money, num } from "../../lib/format";
import {
  BID_TYPES,
  CREATIVE_TYPES,
  type Campaign,
  type AdSet,
  type Creative,
  type Suggestion,
  type SuggestionAction,
} from "../../types";
import CampaignTargeting from "./CampaignTargeting";

function formatSuggestionAction(action: SuggestionAction) {
  if (action.action === "increase_budget_20") return "Increase daily budget by 20%";
  if (action.action === "decrease_budget_20") return "Decrease daily budget by 20%";
  if (action.action === "shift_to_new_geo")
    return action.target_payload?.geo ? `Add ${action.target_payload.geo} to geographic targeting` : "Expand geographic targeting";
  if (action.action === "shift_to_new_sector")
    return action.target_payload?.sector ? `Add ${action.target_payload.sector} to sector targeting` : "Expand sector targeting";
  if (action.action === "improve_creative") return "Improve campaign creative";
  return "AI optimization suggestion";
}

export default function CampaignDetail() {
  const { id } = useParams();
  const campaignId = Number(id);
  const navigate = useNavigate();
  const { session } = useAuth();

  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [adSets, setAdSets] = useState<AdSet[]>([]);
  const [creatives, setCreatives] = useState<Creative[]>([]);
  const [reporting, setReporting] = useState<AdvertiserReporting | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [domains, setDomains] = useState<AdvertiserDomain[]>([]);

  const [updatingSuggestion, setUpdatingSuggestion] = useState<number | null>(null);
  const [suggestionsError, setSuggestionsError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activationLoading, setActivationLoading] = useState(false);
  const [updatingAutopilot, setUpdatingAutopilot] = useState(false);

  // Selected Ad Set for inspection and creative creation
  const [selectedAdSetId, setSelectedAdSetId] = useState<number | null>(null);

  // New Ad Set Modal / Form state
  const [showNewAdSetForm, setShowNewAdSetForm] = useState(false);
  const [newAdSetName, setNewAdSetName] = useState("");
  const [newAdSetBudget, setNewAdSetBudget] = useState("");
  const [newAdSetBid, setNewAdSetBid] = useState("");
  const [adSetSubmitting, setAdSetSubmitting] = useState(false);

  // New Creative Form state
  const [showNewCreativeForm, setShowNewCreativeForm] = useState(false);
  const [creativeTitle, setCreativeTitle] = useState("");
  const [creativeLanding, setCreativeLanding] = useState("");
  const [creativeImage, setCreativeImage] = useState("https://placehold.co/300x250.png");
  const [creativeType, setCreativeType] = useState("1");
  const [creativeWidth, setCreativeWidth] = useState("300");
  const [creativeHeight, setCreativeHeight] = useState("250");
  const [creativeSubmitting, setCreativeSubmitting] = useState(false);

  const [dateRange, setDateRange] = useState<DateRangeState>(() => {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - 7);
    return {
      preset: "7d",
      startDate: start.toISOString().split("T")[0],
      endDate: end.toISOString().split("T")[0],
    };
  });

  const [activeTab, setActiveTab] = useState<"impressions" | "clicks" | "spend" | "conversions">("impressions");

  const reload = async () => {
    if (!campaignId) return;
    try {
      const [nextCampaign, nextAdSets, nextCreatives, nextReporting, nextSuggestions, nextDomains] =
        await Promise.all([
          getCampaign(campaignId),
          listAdSets(campaignId).catch(() => []),
          listCreatives(campaignId).catch(() => []),
          session?.user.advertiser_id
            ? getAdvertiserReporting(session.user.advertiser_id, dateRange.startDate, dateRange.endDate, campaignId).catch(() => null)
            : Promise.resolve(null),
          getCampaignSuggestions(campaignId).catch((err) => {
            setSuggestionsError(err.message || "Failed to load suggestions");
            return [];
          }),
          listAdvertiserDomains().catch(() => []),
        ]);

      setCampaign(nextCampaign);
      setAdSets(nextAdSets);
      setCreatives(nextCreatives);
      if (nextReporting) setReporting(nextReporting);
      setSuggestions(nextSuggestions);
      setDomains(nextDomains);

      if (nextAdSets.length > 0 && !selectedAdSetId) {
        setSelectedAdSetId(nextAdSets[0].id);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not load campaign details");
    }
  };

  useEffect(() => {
    void reload();
  }, [campaignId, dateRange, session?.user.advertiser_id]);

  const toggleAutopilot = async () => {
    if (!campaign || updatingAutopilot) return;
    setUpdatingAutopilot(true);
    try {
      const nextState = !campaign.autopilot;
      const updated = await updateCampaign(campaign.id, { autopilot: nextState });
      setCampaign(updated);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not update autopilot");
    } finally {
      setUpdatingAutopilot(false);
    }
  };

  const handleActivateCampaign = async () => {
    if (!campaign || activationLoading) return;
    setActivationLoading(true);
    setError(null);
    try {
      const activated = await activateCampaign(campaign.id);
      setCampaign(activated);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Campaign activation rejected by server");
    } finally {
      setActivationLoading(false);
    }
  };

  const handleToggleStatus = async () => {
    if (!campaign) return;
    try {
      const nextStatus = campaign.status === 1 ? 0 : 1;
      const updated = await updateCampaign(campaign.id, { status: nextStatus });
      setCampaign(updated);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not update campaign status");
    }
  };

  const handleToggleAdSetStatus = async (adSet: AdSet) => {
    try {
      if (adSet.status === 1) {
        await pauseAdSet(adSet.id);
      } else {
        await resumeAdSet(adSet.id);
      }
      await reload();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not update Ad Set status");
    }
  };

  const handleCreateAdSet = async (e: FormEvent) => {
    e.preventDefault();
    if (!campaign || adSetSubmitting) return;
    if (!newAdSetName.trim()) return;

    setAdSetSubmitting(true);
    try {
      const budgetNum = Number(newAdSetBudget);
      const bidNum = Number(newAdSetBid);
      const created = await createAdSet(campaign.id, {
        name: newAdSetName.trim(),
        daily_budget: isNaN(budgetNum) ? null : budgetNum,
        bid_type: campaign.bid_type,
        bid_amount: isNaN(bidNum) || bidNum <= 0 ? campaign.bid_amount : bidNum,
      });
      setNewAdSetName("");
      setNewAdSetBudget("");
      setNewAdSetBid("");
      setShowNewAdSetForm(false);
      setSelectedAdSetId(created.id);
      await reload();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create Ad Set");
    } finally {
      setAdSetSubmitting(false);
    }
  };

  const handleCreateCreative = async (e: FormEvent) => {
    e.preventDefault();
    if (!campaign || creativeSubmitting) return;
    if (!selectedAdSetId) {
      setError("Please select an Ad Set first.");
      return;
    }
    if (!creativeTitle.trim() || !creativeLanding.trim()) return;

    setCreativeSubmitting(true);
    try {
      await createCreative({
        campaign_id: campaign.id,
        ad_set_id: selectedAdSetId,
        title: creativeTitle.trim(),
        landing_url: creativeLanding.trim(),
        image_url: creativeImage.trim() || undefined,
        creative_type: Number(creativeType),
        width: Number(creativeWidth) || 300,
        height: Number(creativeHeight) || 250,
      });
      setCreativeTitle("");
      setCreativeLanding("");
      setShowNewCreativeForm(false);
      await reload();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not create creative");
    } finally {
      setCreativeSubmitting(false);
    }
  };

  const handleApproveSuggestion = async (sugg: Suggestion) => {
    if (updatingSuggestion) return;
    setUpdatingSuggestion(sugg.id);
    setSuggestionsError(null);
    try {
      await updateSuggestionStatus(sugg.id, "APPLIED");
      await reload();
    } catch (err: any) {
      setSuggestionsError(err.message || "Failed to apply suggestion");
      await reload();
    } finally {
      setUpdatingSuggestion(null);
    }
  };

  const handleRejectSuggestion = async (sugg: Suggestion) => {
    if (updatingSuggestion) return;
    setUpdatingSuggestion(sugg.id);
    setSuggestionsError(null);
    try {
      await updateSuggestionStatus(sugg.id, "REJECTED");
      await reload();
    } catch (err: any) {
      setSuggestionsError(err.message || "Failed to reject suggestion");
      await reload();
    } finally {
      setUpdatingSuggestion(null);
    }
  };

  if (!campaign) {
    return (
      <PageContainer>
        {error ? (
          <Alert variant="error">{error}</Alert>
        ) : (
          <LoadingSkeleton variant="rect" height={220} />
        )}
      </PageContainer>
    );
  }

  // Pacing calculations
  const dailyPacingPct =
    campaign.budget_daily > 0
      ? Math.min(100, Math.round((campaign.spent_today / campaign.budget_daily) * 100))
      : 0;
  const lifetimePacingPct =
    campaign.budget_total > 0
      ? Math.min(100, Math.round((campaign.spent_total / campaign.budget_total) * 100))
      : 0;

  return (
    <PageContainer>
      <PageHeader
        title={`${campaign.name} (${campaign.status === 1 ? "Active" : campaign.status === 5 ? "Draft" : "Paused"})`}
        description={`Campaign ID: ${campaign.id} | Pricing: ${BID_TYPES[campaign.bid_type]} @ $${campaign.bid_amount}`}
        actions={
          <div style={{ display: "flex", gap: "var(--space-2)" }}>
            {campaign.status === 5 ? (
              <Button
                variant="primary"
                onClick={handleActivateCampaign}
                isLoading={activationLoading}
                disabled={activationLoading}
              >
                🚀 Activate Campaign
              </Button>
            ) : (
              <Button variant="outline" onClick={handleToggleStatus}>
                {campaign.status === 1 ? "Pause Campaign" : "Resume Campaign"}
              </Button>
            )}
            <Button
              variant="outline"
              onClick={() => navigate(`/advertiser/campaigns/new?campaign_id=${campaign.id}&step=2`)}
            >
              Wizard Edit
            </Button>
          </div>
        }
      />

      {error && (
        <Alert variant="error" style={{ marginBottom: "var(--space-6)" }}>
          {error}
        </Alert>
      )}

      {/* Date Range Toolbar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-6)" }}>
        <h2 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: 0 }}>
          Performance & Budget Pacing
        </h2>
        <DateRangeSelector value={dateRange} onChange={setDateRange} />
      </div>

      {/* Stat Cards */}
      <div className="grid cards" style={{ marginBottom: "var(--space-6)", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
        <StatCard title="Daily Budget" value={`$${campaign.budget_daily}`} />
        <StatCard title="Total Budget" value={money(campaign.budget_total)} />
        <StatCard title="Impressions" value={reporting ? num(reporting.impressions) : num(campaign.impressions)} />
        <StatCard title="Clicks" value={reporting ? num(reporting.clicks) : num(campaign.clicks)} />
        <StatCard title="CTR" value={reporting ? `${Number(reporting.ctr).toFixed(2)}%` : ctr(campaign.clicks, campaign.impressions)} />
        <StatCard title="Total Spend" value={reporting ? money(reporting.spend) : money(campaign.spent_total)} />
      </div>

      {/* Budget Pacing Bars */}
      <Card style={{ marginBottom: "var(--space-6)" }}>
        <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-3) 0" }}>
          Spend Pacing
        </h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-4)" }}>
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--font-size-xs)" }}>
              <span className="text-muted">Today's Spend Pacing</span>
              <strong>{dailyPacingPct}% of ${campaign.budget_daily}</strong>
            </div>
            <div className="pacing-bar-container">
              <div
                className={`pacing-bar-fill ${dailyPacingPct > 90 ? "danger" : dailyPacingPct > 75 ? "warning" : ""}`}
                style={{ width: `${dailyPacingPct}%` }}
              />
            </div>
            <div className="text-muted" style={{ fontSize: "11px", marginTop: "var(--space-1)" }}>
              Spent today: ${campaign.spent_today.toFixed(2)}
            </div>
          </div>

          <div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--font-size-xs)" }}>
              <span className="text-muted">Total Budget Pacing</span>
              <strong>{lifetimePacingPct}% of ${campaign.budget_total}</strong>
            </div>
            <div className="pacing-bar-container">
              <div
                className={`pacing-bar-fill ${lifetimePacingPct > 95 ? "danger" : lifetimePacingPct > 80 ? "warning" : ""}`}
                style={{ width: `${lifetimePacingPct}%` }}
              />
            </div>
            <div className="text-muted" style={{ fontSize: "11px", marginTop: "var(--space-1)" }}>
              Spent lifetime: ${campaign.spent_total.toFixed(2)}
            </div>
          </div>
        </div>
      </Card>

      {/* Performance Chart */}
      {reporting && (
        <Card style={{ marginBottom: "var(--space-6)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-4)" }}>
            <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: 0 }}>
              Performance Metrics
            </h3>
            <div style={{ display: "flex", gap: "var(--space-2)" }}>
              {(["impressions", "clicks", "spend", "conversions"] as const).map((metric) => (
                <Button
                  key={metric}
                  variant={activeTab === metric ? "primary" : "outline"}
                  size="sm"
                  onClick={() => setActiveTab(metric)}
                  style={{ textTransform: "capitalize" }}
                >
                  {metric}
                </Button>
              ))}
            </div>
          </div>
          <TimeSeriesChart
            data={reporting.daily_breakdown}
            metric={activeTab}
            color={
              activeTab === "impressions"
                ? "#2563eb"
                : activeTab === "clicks"
                ? "#16a34a"
                : activeTab === "spend"
                ? "#d97706"
                : "#8b5cf6"
            }
          />
        </Card>
      )}

      {/* Hierarchy: Ad Sets Section */}
      <Card style={{ marginBottom: "var(--space-6)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-4)" }}>
          <div>
            <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-1) 0" }}>
              Ad Sets ({adSets.length})
            </h3>
            <p className="text-muted" style={{ fontSize: "var(--font-size-xs)", margin: 0 }}>
              Ad Sets enforce sub-budgets and separate targeting pools.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => setShowNewAdSetForm(!showNewAdSetForm)}>
            {showNewAdSetForm ? "Cancel" : "+ Add Ad Set"}
          </Button>
        </div>

        {showNewAdSetForm && (
          <form onSubmit={handleCreateAdSet} style={{ background: "var(--color-bg-subtle)", padding: "var(--space-3)", borderRadius: "var(--radius-md)", marginBottom: "var(--space-4)" }}>
            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr auto", gap: "var(--space-2)", alignItems: "end" }}>
              <Input
                label="Ad Set Name *"
                placeholder="e.g. Mobile Tech Enthusiasts"
                value={newAdSetName}
                onChange={(e) => setNewAdSetName(e.target.value)}
                required
              />
              <Input
                label="Daily Sub-Budget ($)"
                type="number"
                placeholder={String(campaign.budget_daily)}
                value={newAdSetBudget}
                onChange={(e) => setNewAdSetBudget(e.target.value)}
              />
              <Input
                label="Bid Amount ($)"
                type="number"
                placeholder={String(campaign.bid_amount)}
                value={newAdSetBid}
                onChange={(e) => setNewAdSetBid(e.target.value)}
              />
              <Button type="submit" variant="primary" size="sm" isLoading={adSetSubmitting}>
                Save Ad Set
              </Button>
            </div>
          </form>
        )}

        {adSets.length === 0 ? (
          <p className="text-muted" style={{ textAlign: "center", padding: "var(--space-4)" }}>
            No Ad Sets created yet. Create an Ad Set to configure targeting and serve ads.
          </p>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: "var(--space-3)" }}>
            {adSets.map((adSet) => {
              const isSelected = adSet.id === selectedAdSetId;
              const setCreativesCount = creatives.filter((c) => c.ad_set_id === adSet.id).length;
              return (
                <div
                  key={adSet.id}
                  onClick={() => setSelectedAdSetId(adSet.id)}
                  style={{
                    border: `2px solid ${isSelected ? "var(--color-primary)" : "var(--color-border)"}`,
                    background: isSelected ? "var(--color-bg-subtle)" : "var(--color-bg-surface)",
                    borderRadius: "var(--radius-lg)",
                    padding: "var(--space-3)",
                    cursor: "pointer",
                    transition: "all var(--transition-fast)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-2)" }}>
                    <strong style={{ fontSize: "var(--font-size-sm)" }}>{adSet.name}</strong>
                    <StatusBadge status={adSet.status} />
                  </div>
                  <div className="text-muted" style={{ fontSize: "var(--font-size-xs)", marginBottom: "var(--space-2)" }}>
                    Daily: ${adSet.daily_budget != null ? adSet.daily_budget : campaign.budget_daily} | Bid: ${adSet.bid_amount} | {setCreativesCount} Ads
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span className="badge badge-subtle" style={{ fontSize: "10px" }}>
                      Mode: {adSet.delivery_mode || "standard"}
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        void handleToggleAdSetStatus(adSet);
                      }}
                    >
                      {adSet.status === 1 ? "Pause" : "Resume"}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {/* Creatives (Ads) Section */}
      <Card style={{ marginBottom: "var(--space-6)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-4)" }}>
          <div>
            <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-1) 0" }}>
              Ads & Creatives ({creatives.length})
            </h3>
            <p className="text-muted" style={{ fontSize: "var(--font-size-xs)", margin: 0 }}>
              {selectedAdSetId
                ? `Showing ads for selected Ad Set: ${adSets.find((a) => a.id === selectedAdSetId)?.name || selectedAdSetId}`
                : "All creatives in campaign"}
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => setShowNewCreativeForm(!showNewCreativeForm)}>
            {showNewCreativeForm ? "Cancel" : "+ Add Ad to Set"}
          </Button>
        </div>

        {showNewCreativeForm && (
          <form onSubmit={handleCreateCreative} style={{ background: "var(--color-bg-subtle)", padding: "var(--space-4)", borderRadius: "var(--radius-md)", marginBottom: "var(--space-4)" }}>
            <h4 style={{ fontSize: "var(--font-size-sm)", margin: "0 0 var(--space-3) 0" }}>New Creative</h4>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-3)", marginBottom: "var(--space-3)" }}>
              <Select
                label="Target Ad Set *"
                value={String(selectedAdSetId || "")}
                onChange={(e) => setSelectedAdSetId(Number(e.target.value))}
                options={adSets.map((a) => ({ value: String(a.id), label: a.name }))}
                required
              />
              <Input
                label="Headline / Title *"
                placeholder="Ad headline"
                value={creativeTitle}
                onChange={(e) => setCreativeTitle(e.target.value)}
                required
              />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: "var(--space-3)", marginBottom: "var(--space-3)" }}>
              <Input
                label="HTTPS Landing URL *"
                placeholder="https://example.com"
                value={creativeLanding}
                onChange={(e) => setCreativeLanding(e.target.value)}
                required
              />
              <Input
                label="Width (px)"
                type="number"
                value={creativeWidth}
                onChange={(e) => setCreativeWidth(e.target.value)}
              />
              <Input
                label="Height (px)"
                type="number"
                value={creativeHeight}
                onChange={(e) => setCreativeHeight(e.target.value)}
              />
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-2)" }}>
              <Button type="button" variant="ghost" size="sm" onClick={() => setShowNewCreativeForm(false)}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" size="sm" isLoading={creativeSubmitting}>
                Save Creative
              </Button>
            </div>
          </form>
        )}

        {creatives.length === 0 ? (
          <p className="text-muted" style={{ textAlign: "center", padding: "var(--space-4)" }}>
            No creatives added to this campaign yet.
          </p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="table" style={{ width: "100%", textAlign: "left" }}>
              <thead>
                <tr>
                  <th>Creative Title</th>
                  <th>Ad Set</th>
                  <th>Format & Size</th>
                  <th>Moderation Status</th>
                  <th>Landing Domain</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {creatives.map((c) => {
                  const setObj = adSets.find((a) => a.id === c.ad_set_id);
                  let domainHost = "";
                  try {
                    domainHost = new URL(c.landing_url).hostname.toLowerCase();
                  } catch {}
                  const isVerified = domains.some((d) => d.status === "VERIFIED" && d.domain.toLowerCase() === domainHost);

                  return (
                    <tr key={c.id}>
                      <td>
                        <strong>{c.title}</strong>
                        {c.description && (
                          <div className="text-muted" style={{ fontSize: "11px" }}>{c.description}</div>
                        )}
                      </td>
                      <td>{setObj?.name || "Default"}</td>
                      <td>
                        {CREATIVE_TYPES[c.creative_type] || "Banner"} ({c.width}x{c.height})
                      </td>
                      <td>
                        {c.moderation_status === 2 ? (
                          <span className="chip chip-include">Approved</span>
                        ) : c.moderation_status === 3 ? (
                          <span className="chip chip-exclude">Rejected: {c.moderation_reason || "Violation"}</span>
                        ) : (
                          <span className="chip" style={{ background: "var(--color-warning-subtle)", color: "var(--color-warning-text)" }}>
                            Pending Review
                          </span>
                        )}
                      </td>
                      <td>
                        <span style={{ fontSize: "var(--font-size-xs)", color: isVerified ? "var(--color-success-text)" : "var(--color-warning-text)" }}>
                          {isVerified ? `✓ ${domainHost}` : `⚠ ${domainHost || "Unverified"}`}
                        </span>
                      </td>
                      <td>
                        <a
                          href={c.landing_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="btn btn-outline btn-sm"
                        >
                          Visit ↗
                        </a>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* AI Autopilot Card */}
      <Card style={{ marginBottom: "var(--space-6)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-1) 0" }}>
              AI Autopilot Optimization
            </h3>
            <p className="text-muted" style={{ fontSize: "var(--font-size-xs)", margin: 0 }}>
              When enabled, automated algorithms adjust daily pacing, expand high-converting geo targets, and prune underperforming creatives within campaign constraints.
            </p>
          </div>
          <Button
            variant={campaign.autopilot ? "primary" : "outline"}
            onClick={toggleAutopilot}
            disabled={updatingAutopilot}
            isLoading={updatingAutopilot}
          >
            {campaign.autopilot ? "Autopilot: ACTIVE" : "Autopilot: OFF"}
          </Button>
        </div>
      </Card>

      {/* AI Suggestions Card */}
      <Card style={{ marginBottom: "var(--space-6)" }}>
        <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-2) 0" }}>
          Optimization Recommendations ({suggestions.length})
        </h3>
        {suggestionsError && (
          <Alert variant="error" style={{ marginBottom: "var(--space-3)" }}>
            {suggestionsError}
          </Alert>
        )}

        {suggestions.length === 0 ? (
          <p className="text-muted" style={{ margin: 0 }}>
            No pending AI suggestions at this time. Recommendations appear as performance signals accumulate.
          </p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
            {suggestions.map((sugg) => (
              <div
                key={sugg.id}
                style={{
                  border: "1px solid var(--color-border)",
                  borderRadius: "var(--radius-md)",
                  padding: "var(--space-3)",
                  background: "var(--color-bg-subtle)",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <strong style={{ fontSize: "var(--font-size-sm)" }}>{formatSuggestionAction(sugg.action)}</strong>
                  <div className="text-muted" style={{ fontSize: "var(--font-size-xs)", marginTop: "var(--space-1)" }}>
                    Confidence: {sugg.action.confidence ? `${Math.round(sugg.action.confidence * 100)}%` : "N/A"}
                    {sugg.action.proposed_budget_delta != null && (
                      <span style={{ marginLeft: "var(--space-2)" }}>
                        Budget Delta: {sugg.action.proposed_budget_delta > 0 ? "+" : ""}${sugg.action.proposed_budget_delta}
                      </span>
                    )}
                  </div>
                </div>

                <div style={{ display: "flex", gap: "var(--space-2)" }}>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={updatingSuggestion === sugg.id}
                    onClick={() => handleRejectSuggestion(sugg)}
                  >
                    Reject
                  </Button>
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={updatingSuggestion === sugg.id}
                    isLoading={updatingSuggestion === sugg.id}
                    onClick={() => handleApproveSuggestion(sugg)}
                  >
                    Apply Suggestion
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Targeting Component */}
      <CampaignTargeting campaignId={campaignId} adSets={adSets} />
    </PageContainer>
  );
}
