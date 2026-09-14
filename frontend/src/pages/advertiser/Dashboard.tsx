import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listCampaigns } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import Badge from "../../components/Badge";
import StatCard from "../../components/StatCard";
import { ctr, money, num } from "../../lib/format";
import { BID_TYPES, type Campaign } from "../../types";

export default function AdvertiserDashboard() {
	const { session } = useAuth();
	const [rows, setRows] = useState<Campaign[]>([]);
	const [error, setError] = useState<string | null>(null);
	useEffect(() => { if (session?.user.advertiser_id) void listCampaigns(session.user.advertiser_id).then(setRows).catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load campaigns")); }, [session?.user.advertiser_id]);
	const active = rows.filter((campaign) => campaign.status === 1).length;
	const impressions = rows.reduce((sum, campaign) => sum + campaign.impressions, 0);
	const clicks = rows.reduce((sum, campaign) => sum + campaign.clicks, 0);
	return <div><h1>My Campaigns</h1>{error && <p className="error">{error}</p>}<div className="grid cards"><StatCard title="Active campaigns" value={`${active} / ${rows.length}`} /><StatCard title="Budget today" value={money(rows.reduce((sum, campaign) => sum + campaign.budget_daily, 0))} /><StatCard title="Impressions" value={num(impressions)} /><StatCard title="CTR" value={ctr(clicks, impressions)} /></div><div className="card"><table className="table"><thead><tr><th>Name</th><th>Bid</th><th>Spent today</th><th>Impr.</th><th>Clicks</th><th>CTR</th><th>Conv.</th><th>Status</th><th /></tr></thead><tbody>{rows.map((campaign) => <tr key={campaign.id}><td><b>{campaign.name}</b></td><td>{BID_TYPES[campaign.bid_type]} ${campaign.bid_amount}</td><td>{money(campaign.spent_today)} / {money(campaign.budget_daily)}</td><td>{num(campaign.impressions)}</td><td>{num(campaign.clicks)}</td><td>{ctr(campaign.clicks, campaign.impressions)}</td><td>{num(campaign.conversions)}</td><td><Badge status={campaign.status} /></td><td><Link className="btn secondary small" to={`/advertiser/campaigns/${campaign.id}`}>View</Link></td></tr>)}</tbody></table>{!rows.length && !error && <p className="muted">No campaigns yet. Create your first one.</p>}</div></div>;
}
