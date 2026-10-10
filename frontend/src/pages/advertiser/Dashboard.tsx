import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { 
    listCampaigns, 
    getAdvertiserLedger, 
    getAdvertiserReporting,
    depositFunds,
    type LedgerResponse,
    type AdvertiserReporting
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import Badge from "../../components/Badge";
import StatCard from "../../components/StatCard";
import DateRangeSelector, { type DateRangeState } from "../../components/DateRangeSelector";
import TimeSeriesChart from "../../components/TimeSeriesChart";
import { ctr, money, num } from "../../lib/format";
import { BID_TYPES, type Campaign } from "../../types";

export default function AdvertiserDashboard() {
    const { session } = useAuth();
    const [rows, setRows] = useState<Campaign[]>([]);
    const [ledger, setLedger] = useState<LedgerResponse | null>(null);
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
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);

    const loadData = async () => {
        if (!session?.user.advertiser_id) return;
        setLoading(true);
        setError(null);
        try {
            const [campaignsRes, ledgerRes, reportingRes] = await Promise.all([
                listCampaigns(session.user.advertiser_id),
                getAdvertiserLedger(session.user.advertiser_id),
                getAdvertiserReporting(session.user.advertiser_id, dateRange.startDate, dateRange.endDate)
            ]);
            setRows(campaignsRes);
            setLedger(ledgerRes);
            setReporting(reportingRes);
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Could not load dashboard data");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { void loadData(); }, [session?.user.advertiser_id, dateRange]);

    const active = rows.filter((campaign) => campaign.status === 1).length;

    const [depositAmount, setDepositAmount] = useState<number | "">("");
    
    const handleDeposit = async () => {
        if (!depositAmount || depositAmount <= 0) return;
        if (!session?.user.advertiser_id) return;
        try {
            await depositFunds(session.user.advertiser_id, Number(depositAmount), `demo_dep_${Date.now()}`);
            setDepositAmount("");
            loadData();
        } catch (err) {
            alert("Deposit failed: " + (err instanceof Error ? err.message : String(err)));
        }
    };
    
    return (
        <div>
            <div className="flex row space-between align-center" style={{ marginBottom: "24px" }}>
                <h1>Dashboard</h1>
                <DateRangeSelector value={dateRange} onChange={setDateRange} />
            </div>

            {error && <p className="error" style={{ marginBottom: "24px" }}>{error}</p>}

            {loading && !reporting ? (
                <div style={{ padding: "40px", textAlign: "center" }} className="muted">Loading dashboard data...</div>
            ) : reporting ? (
                <>
                    <div className="grid cards" style={{ marginBottom: "24px", gridTemplateColumns: "repeat(5, 1fr)" }}>
                        <StatCard title="Impressions" value={num(reporting.impressions)} />
                        <StatCard title="Clicks" value={num(reporting.clicks)} />
                        <StatCard title="CTR" value={`${Number(reporting.ctr).toFixed(2)}%`} />
                        <StatCard title="Conversions" value={num(reporting.conversions)} />
                        <StatCard title="Spend" value={money(reporting.spend)} />
                    </div>

                    <div className="card" style={{ marginBottom: "32px" }}>
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
                </>
            ) : null}

            <div className="grid" style={{ gridTemplateColumns: "3fr 1fr", gap: "24px", alignItems: "start" }}>
                <div>
                    <div className="flex row space-between align-center" style={{ marginBottom: "16px" }}>
                        <h2>Campaign Performance</h2>
                        <div className="actions">
                            <Link className="btn primary" to="/advertiser/campaign-builder">New Campaign (AI)</Link>
                            <Link className="btn secondary" to="/advertiser/campaigns/new">Manual</Link>
                        </div>
                    </div>
                    <div className="card">
                        <table className="table">
                            <thead>
                                <tr><th>Name</th><th>Status</th><th>Spend / Budget</th><th>Impr.</th><th>Clicks</th><th>CTR</th><th /></tr>
                            </thead>
                            <tbody>
                                {rows.map((campaign) => <tr key={campaign.id}>
                                    <td><b>{campaign.name}</b></td>
                                    <td><Badge status={campaign.status} /></td>
                                    <td>{money(campaign.spent_total)} / {money(campaign.budget_total)}</td>
                                    <td>{num(campaign.impressions)}</td>
                                    <td>{num(campaign.clicks)}</td>
                                    <td>{ctr(campaign.clicks, campaign.impressions)}</td>
                                    <td><Link className="btn secondary small" to={`/advertiser/campaigns/${campaign.id}`}>View</Link></td>
                                </tr>)}
                            </tbody>
                        </table>
                        {!loading && !rows.length && !error && <p className="muted" style={{ padding: "20px", textAlign: "center" }}>No campaigns yet. Create your first one.</p>}
                    </div>
                </div>

                <div>
                    <h2 style={{ marginBottom: "16px" }}>Wallet & Ledger</h2>
                    <div className="card" style={{ marginBottom: "16px" }}>
                        <div style={{ fontSize: "28px", fontWeight: "bold", marginBottom: "8px" }}>
                            {ledger ? money(ledger.balance) : "..."}
                        </div>
                        <div className="muted" style={{ fontSize: "14px", marginBottom: "16px" }}>
                            Active Campaigns: {active} / {rows.length}
                        </div>
                        <div className="flex row gap" style={{ alignItems: "center" }}>
                            <input type="number" placeholder="Amount ($)" value={depositAmount} onChange={e => setDepositAmount(Number(e.target.value) || "")} min={1} style={{ flex: 1, padding: "8px", border: "1px solid var(--border)", borderRadius: "4px" }} />
                            <button className="btn primary" onClick={handleDeposit} disabled={!depositAmount}>Deposit</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
