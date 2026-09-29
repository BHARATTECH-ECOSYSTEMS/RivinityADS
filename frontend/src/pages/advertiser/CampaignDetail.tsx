import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { createCreative, getCampaign, listCreatives, updateCampaign, getAdvertiserReporting, type AdvertiserReporting } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import Badge from "../../components/Badge";
import StatCard from "../../components/StatCard";
import DateRangeSelector, { type DateRangeState } from "../../components/DateRangeSelector";
import TimeSeriesChart from "../../components/TimeSeriesChart";
import { ctr, money, num } from "../../lib/format";
import { BID_TYPES, CREATIVE_TYPES, type Campaign, type Creative } from "../../types";
import CampaignTargeting from "./CampaignTargeting";

export default function CampaignDetail() {
  const { id } = useParams();
  const campaignId = Number(id);
  const { session } = useAuth();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [creatives, setCreatives] = useState<Creative[]>([]);
  const [reporting, setReporting] = useState<AdvertiserReporting | null>(null);
  
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

  const reload = async () => {
    try {
      const [nextCampaign, nextCreatives, nextReporting] = await Promise.all([
          getCampaign(campaignId), 
          listCreatives(campaignId),
          session?.user.advertiser_id ? getAdvertiserReporting(session.user.advertiser_id, dateRange.startDate, dateRange.endDate, campaignId) : Promise.resolve(null)
      ]);
      setCampaign(nextCampaign);
      setCreatives(nextCreatives);
      if (nextReporting) setReporting(nextReporting);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load campaign");
    }
  };

  useEffect(() => { void reload(); }, [campaignId, dateRange, session?.user.advertiser_id]);

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
    
    <CampaignTargeting campaignId={campaignId} />

    <p><Link to="/advertiser">Back to campaigns</Link></p>
  </div>;
}
