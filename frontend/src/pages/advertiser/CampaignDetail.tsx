import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { createCreative, getCampaign, listCreatives, updateCampaign, getAdvertiserReporting, type AdvertiserReporting, getCampaignSuggestions, updateSuggestionStatus } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import Badge from "../../components/Badge";
import StatCard from "../../components/StatCard";
import DateRangeSelector, { type DateRangeState } from "../../components/DateRangeSelector";
import TimeSeriesChart from "../../components/TimeSeriesChart";
import { ctr, money, num } from "../../lib/format";
import { BID_TYPES, CREATIVE_TYPES, type Campaign, type Creative, type Suggestion, type SuggestionAction } from "../../types";
import CampaignTargeting from "./CampaignTargeting";

function formatSuggestionAction(action: SuggestionAction) {
    if (action.action === "increase_budget_20") return "Increase daily budget by 20%";
    if (action.action === "decrease_budget_20") return "Decrease daily budget by 20%";
    if (action.action === "shift_to_new_geo") return action.target_payload?.geo ? `Add ${action.target_payload.geo} to geographic targeting` : "Expand geographic targeting";
    if (action.action === "shift_to_new_sector") return action.target_payload?.sector ? `Add ${action.target_payload.sector} to sector targeting` : "Expand sector targeting";
    if (action.action === "improve_creative") return "Improve campaign creative";
    return "AI optimization suggestion";
}

export default function CampaignDetail() {
  const { id } = useParams();
  const campaignId = Number(id);
  const { session } = useAuth();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [creatives, setCreatives] = useState<Creative[]>([]);
  const [reporting, setReporting] = useState<AdvertiserReporting | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [updatingSuggestion, setUpdatingSuggestion] = useState<number | null>(null);
  const [suggestionsError, setSuggestionsError] = useState<string | null>(null);
  
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
  const [title, setTitle] = useState("");
  const [landing, setLanding] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [updatingAutopilot, setUpdatingAutopilot] = useState(false);

  const toggleAutopilot = async () => {
    if (!campaign || updatingAutopilot) return;
    setUpdatingAutopilot(true);
    try {
      const nextState = !campaign.autopilot;
      const updated = await updateCampaign(campaign.id, { autopilot: nextState });
      setCampaign(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update autopilot");
    } finally {
      setUpdatingAutopilot(false);
    }
  };

  const reload = async () => {
    try {
      const [nextCampaign, nextCreatives, nextReporting, nextSuggestions] = await Promise.all([
          getCampaign(campaignId), 
          listCreatives(campaignId),
          session?.user.advertiser_id ? getAdvertiserReporting(session.user.advertiser_id, dateRange.startDate, dateRange.endDate, campaignId) : Promise.resolve(null),
          getCampaignSuggestions(campaignId).catch(err => {
              setSuggestionsError(err.message || "Failed to load suggestions");
              return [];
          })
      ]);
      setCampaign(nextCampaign);
      setCreatives(nextCreatives);
      if (nextReporting) setReporting(nextReporting);
      setSuggestions(nextSuggestions);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load campaign");
    }
  };

  useEffect(() => { void reload(); }, [campaignId, dateRange, session?.user.advertiser_id]);

  const handleApprove = async (sugg: Suggestion) => {
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

  const handleReject = async (sugg: Suggestion) => {
      if (updatingSuggestion) return;
      setUpdatingSuggestion(sugg.id);
      setSuggestionsError(null);
      try {
          await updateSuggestionStatus(sugg.id, "REJECTED");
          const nextSuggestions = await getCampaignSuggestions(campaignId).catch(() => []);
          setSuggestions(nextSuggestions);
      } catch (err: any) {
          setSuggestionsError(err.message || "Failed to reject suggestion");
          const nextSuggestions = await getCampaignSuggestions(campaignId).catch(() => []);
          setSuggestions(nextSuggestions);
      } finally {
          setUpdatingSuggestion(null);
      }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await createCreative({ campaign_id: campaignId, title, description: "", image_url: "https://placehold.co/300x250", landing_url: landing, creative_type: 1, width: 300, height: 250 });
      setTitle("");
      setLanding("");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create creative");
    }
  };

  if (!campaign) return <div>{error ? <p className="error">{error}</p> : <p className="muted">Loading...</p>}</div>;

  return <div>
    <div className="flex row space-between align-center">
        <h1>{campaign.name} <Badge status={campaign.status} /></h1>
        <DateRangeSelector value={dateRange} onChange={setDateRange} />
    </div>
    
    {error && <p className="error">{error}</p>}
    <p className="muted">{BID_TYPES[campaign.bid_type]} bid ${campaign.bid_amount} <button className="btn secondary small" onClick={() => void updateCampaign(campaign.id, { status: campaign.status === 1 ? 0 : 1 }).then(reload)}>Toggle status</button></p>
    
    <div className="grid cards">
        <StatCard title="Budget" value={money(campaign.budget_total)} />
        <StatCard title="Impressions" value={reporting ? num(reporting.impressions) : num(campaign.impressions)} />
        <StatCard title="Clicks" value={reporting ? num(reporting.clicks) : num(campaign.clicks)} />
        <StatCard title="Conversions" value={reporting ? num(reporting.conversions) : num(campaign.conversions)} />
        <StatCard title="Spend" value={reporting ? money(reporting.spend) : money(campaign.spent_total)} />
    </div>
    
    {reporting && (
        <div className="card" style={{ marginBottom: "24px" }}>
            <div className="flex row gap" style={{ marginBottom: "16px" }}>
                {(["impressions", "clicks", "spend", "conversions"] as const).map(metric => (
                    <button 
                        key={metric}
                        className={`btn ${activeTab === metric ? "primary" : "secondary"}`}
                        onClick={() => setActiveTab(metric)}
                        style={{ textTransform: "capitalize" }}
                    >
                        {metric}
                    </button>
                ))}
            </div>
            <TimeSeriesChart 
                data={reporting.daily_breakdown} 
                metric={activeTab} 
                color={
                    activeTab === "impressions" ? "#0066cc" : 
                    activeTab === "clicks" ? "#10b981" : 
                    activeTab === "spend" ? "#f59e0b" : "#8b5cf6"
                } 
            />
        </div>
    )}
    
    <div className="card"><h2>Creatives</h2><table className="table"><thead><tr><th>ID</th><th>Title</th><th>Type</th><th>Size</th><th>Quality</th></tr></thead><tbody>{creatives.map((creative) => <tr key={creative.id}><td>{creative.id}</td><td><b>{creative.title}</b></td><td>{CREATIVE_TYPES[creative.creative_type]}</td><td>{creative.width}x{creative.height}</td><td>{creative.quality_score}/100</td></tr>)}</tbody></table><form onSubmit={submit}><input placeholder="Title" value={title} onChange={(event) => setTitle(event.target.value)} required /><input type="url" placeholder="Landing URL" value={landing} onChange={(event) => setLanding(event.target.value)} required /><button className="btn">Add creative</button></form></div>
    
    <div className="card">
        <h2>AI AUTOPILOT</h2>
        <p className="muted">Automatically optimize this campaign based on performance.</p>
        <div className="flex row align-center gap">
            <span style={{ fontWeight: 600 }}>Autopilot</span>
            <button 
                className={`btn ${campaign.autopilot ? 'primary' : 'secondary'}`} 
                onClick={() => void toggleAutopilot()} 
                disabled={updatingAutopilot}
            >
                {updatingAutopilot ? "Updating..." : (campaign.autopilot ? "ON" : "OFF")}
            </button>
        </div>
    </div>

    <div className="card">
        <h2>AI SUGGESTIONS</h2>
        <p className="muted">Pending optimization recommendations for this campaign.</p>
        {suggestionsError && <p className="error">{suggestionsError}</p>}
        {suggestions.length === 0 ? (
            <p className="muted">No pending AI suggestions.</p>
        ) : (
            <div className="flex column gap">
                {suggestions.map(sugg => (
                    <div key={sugg.id} className="card" style={{ background: "var(--bg-secondary)", padding: "16px", margin: "0" }}>
                        <div className="flex row space-between align-center">
                            <div>
                                <h3 style={{ margin: "0 0 8px 0", fontSize: "1.1rem" }}>{formatSuggestionAction(sugg.action)}</h3>
                                {sugg.action.confidence && <p className="muted" style={{ margin: "0 0 4px 0" }}>AI confidence: {Math.round(sugg.action.confidence * 100)}%</p>}
                                {sugg.action.proposed_budget_delta != null && <p className="muted" style={{ margin: 0 }}>Proposed budget change: {sugg.action.proposed_budget_delta > 0 ? "+" : ""}{sugg.action.proposed_budget_delta}</p>}
                            </div>
                            <div className="flex row gap">
                                <button className="btn secondary small" disabled={updatingSuggestion === sugg.id} onClick={() => void handleReject(sugg)}>{updatingSuggestion === sugg.id ? "..." : "Reject"}</button>
                                <button className="btn primary small" disabled={updatingSuggestion === sugg.id} onClick={() => void handleApprove(sugg)}>{updatingSuggestion === sugg.id ? "Approving..." : "Approve"}</button>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        )}
    </div>

    <CampaignTargeting campaignId={campaignId} />

    <p><Link to="/advertiser">Back to campaigns</Link></p>
  </div>;
}
