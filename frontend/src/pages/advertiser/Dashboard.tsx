import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listCampaigns, getAdvertiserLedger, depositFunds, type LedgerResponse } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import Badge from "../../components/Badge";
import StatCard from "../../components/StatCard";
import { ctr, money, num, dateString } from "../../lib/format";
import { BID_TYPES, type Campaign } from "../../types";

export default function AdvertiserDashboard() {
	const { session } = useAuth();
	const [rows, setRows] = useState<Campaign[]>([]);
    const [ledger, setLedger] = useState<LedgerResponse | null>(null);
    const [depositAmount, setDepositAmount] = useState<number | "">("");
	const [error, setError] = useState<string | null>(null);

	const loadData = () => {
        if (session?.user.advertiser_id) {
            listCampaigns(session.user.advertiser_id).then(setRows).catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load campaigns"));
            getAdvertiserLedger(session.user.advertiser_id).then(setLedger).catch(console.error);
        }
    };

    useEffect(() => { loadData(); }, [session?.user.advertiser_id]);
    
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

	const active = rows.filter((campaign) => campaign.status === 1).length;
	const impressions = rows.reduce((sum, campaign) => sum + campaign.impressions, 0);
	const clicks = rows.reduce((sum, campaign) => sum + campaign.clicks, 0);

	return (
        <div>
            <h1>Dashboard</h1>
            {error && <p className="error">{error}</p>}
            
            <div className="grid cards" style={{ marginBottom: "24px" }}>
                <StatCard title="Wallet Balance" value={ledger ? money(ledger.balance) : "..."} />
                <StatCard title="Active campaigns" value={`${active} / ${rows.length}`} />
                <StatCard title="Budget today" value={money(rows.reduce((sum, campaign) => sum + campaign.budget_daily, 0))} />
                <StatCard title="Impressions" value={num(impressions)} />
            </div>

            <div className="grid" style={{ gridTemplateColumns: "2fr 1fr", gap: "24px", alignItems: "start" }}>
                <div>
                    <div className="flex row space-between align-center" style={{ marginBottom: "16px" }}>
                        <h2>My Campaigns</h2>
                    </div>
                    <div className="card">
                        <table className="table">
                            <thead>
                                <tr><th>Name</th><th>Bid</th><th>Spent today</th><th>Impr.</th><th>Clicks</th><th>CTR</th><th>Status</th><th /></tr>
                            </thead>
                            <tbody>
                                {rows.map((campaign) => <tr key={campaign.id}>
                                    <td><b>{campaign.name}</b></td>
                                    <td>{BID_TYPES[campaign.bid_type]} ${campaign.bid_amount}</td>
                                    <td>{money(campaign.spent_today)} / {money(campaign.budget_daily)}</td>
                                    <td>{num(campaign.impressions)}</td>
                                    <td>{num(campaign.clicks)}</td>
                                    <td>{ctr(campaign.clicks, campaign.impressions)}</td>
                                    <td><Badge status={campaign.status} /></td>
                                    <td><Link className="btn secondary small" to={`/advertiser/campaigns/${campaign.id}`}>View</Link></td>
                                </tr>)}
                            </tbody>
                        </table>
                        {!rows.length && !error && <p className="muted">No campaigns yet. Create your first one.</p>}
                    </div>
                </div>

                <div>
                    <h2 style={{ marginBottom: "16px" }}>Wallet & Ledger</h2>
                    <div className="card" style={{ marginBottom: "16px" }}>
                        <div className="flex row gap" style={{ alignItems: "center" }}>
                            <input type="number" placeholder="Amount ($)" value={depositAmount} onChange={e => setDepositAmount(Number(e.target.value) || "")} min={1} style={{ flex: 1 }} />
                            <button className="btn primary" onClick={handleDeposit} disabled={!depositAmount}>Deposit Funds</button>
                        </div>
                    </div>
                    <div className="card">
                        <h3 style={{ marginBottom: "12px", fontSize: "14px" }}>Recent Transactions</h3>
                        {ledger?.transactions.length === 0 && <p className="muted" style={{ fontSize: "13px" }}>No transactions yet.</p>}
                        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                            {ledger?.transactions.slice(0, 10).map(tx => (
                                <div key={tx.id} className="flex row space-between align-center" style={{ fontSize: "13px", paddingBottom: "8px", borderBottom: "1px solid var(--border)" }}>
                                    <div>
                                        <div style={{ fontWeight: 600 }}>{tx.type === 1 ? "Deposit" : tx.type === 2 ? "Spend" : "Other"}</div>
                                        <div className="muted">{dateString(tx.created_at)}</div>
                                    </div>
                                    <div style={{ fontWeight: 700, color: tx.amount > 0 ? "var(--success)" : "var(--fg)" }}>
                                        {tx.amount > 0 ? "+" : ""}{money(tx.amount)}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
