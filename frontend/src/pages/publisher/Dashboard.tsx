import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { 
    getPublisherLedger, 
    requestPayout, 
    getPublisherReporting, 
    listPublisherAssignments, 
    type LedgerResponse, 
    type PublisherReporting, 
    type PublisherAssignment 
} from "../../api/client";
import StatCard from "../../components/StatCard";
import DateRangeSelector, { type DateRangeState } from "../../components/DateRangeSelector";
import TimeSeriesChart from "../../components/TimeSeriesChart";
import { money, dateString, num } from "../../lib/format";

export default function PublisherDashboard() {
  const { session } = useAuth();
  
  const [reporting, setReporting] = useState<PublisherReporting | null>(null);
  const [ledger, setLedger] = useState<LedgerResponse | null>(null);
  const [assignments, setAssignments] = useState<PublisherAssignment[]>([]);
  const [payoutAmount, setPayoutAmount] = useState<number | "">("");
  
  const [dateRange, setDateRange] = useState<DateRangeState>(() => {
      const end = new Date();
      const start = new Date();
      start.setDate(end.getDate() - 30);
      return {
          preset: "30d",
          startDate: start.toISOString().split("T")[0],
          endDate: end.toISOString().split("T")[0],
      };
  });

  const [activeTab, setActiveTab] = useState<"impressions" | "clicks" | "earnings" | "conversions">("impressions");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const publisherId = session?.user.publisher_id;

  const reload = async () => {
    if (!publisherId) return;
    setLoading(true);
    setError(null);
    try {
      // Core reporting data first
      const reportData = await getPublisherReporting(publisherId, dateRange.startDate, dateRange.endDate);
      setReporting(reportData);

      // Fetch non-blocking data independently
      getPublisherLedger(publisherId).then(setLedger).catch(err => {
        console.error("Ledger fetch failed:", err);
        setError(err instanceof Error ? err.message : "Failed to load ledger");
      });

      listPublisherAssignments().then(setAssignments).catch(err => {
        console.error("Assignments fetch failed:", err);
        // Do not set global error for assignments failure to allow ledger/reporting to show
      });
      
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void reload(); }, [publisherId, dateRange]);

  const handlePayout = async () => {
      if (!payoutAmount || payoutAmount <= 0) return;
      if (!publisherId) return;
      try {
          await requestPayout(publisherId, Number(payoutAmount), `demo_out_${Date.now()}`);
          setPayoutAmount("");
          await reload();
          alert("Payout requested successfully!");
      } catch (err: any) {
          alert("Payout failed: " + (err.message || "Unknown error"));
      }
  };

  return (
    <div>
      <div className="flex row space-between align-center" style={{ marginBottom: "24px" }}>
        <div>
            <h1>Dashboard</h1>
            <p className="muted">Welcome back! Here's how your inventory is performing.</p>
        </div>
        <DateRangeSelector value={dateRange} onChange={setDateRange} />
      </div>

      {error && <p className="error" style={{ marginBottom: "24px" }}>{error}</p>}

      {loading && !reporting ? (
          <div style={{ padding: "40px", textAlign: "center" }} className="muted">Loading dashboard data...</div>
      ) : reporting ? (
          <>
              <div className="grid cards" style={{ marginBottom: "24px", gridTemplateColumns: "repeat(4, 1fr)" }}>
                <StatCard title="Impressions" value={num(reporting.impressions)} />
                <StatCard title="Clicks" value={num(reporting.clicks)} />
                <StatCard title="Average eCPM" value={money(reporting.ecpm)} />
                <StatCard title="Period Earnings" value={money(reporting.earnings)} />
              </div>

              <div className="card" style={{ marginBottom: "32px" }}>
                  <div className="flex row gap" style={{ marginBottom: "16px" }}>
                      {(["impressions", "clicks", "earnings", "conversions"] as const).map(metric => (
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
                          activeTab === "earnings" ? "#f59e0b" : "#8b5cf6"
                      } 
                  />
              </div>
          </>
      ) : null}

      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: "24px", alignItems: "start" }}>
        
        {/* Earnings & Payouts */}
        <div className="card">
          <div className="flex row space-between align-center" style={{ marginBottom: "24px" }}>
            <h2>Earnings & Wallet</h2>
            <div style={{ fontSize: "24px", fontWeight: "bold", color: "#10b981" }}>
              {ledger ? money(ledger.balance) : "..."}
            </div>
          </div>
          
          <div className="flex row gap align-center" style={{ marginBottom: "24px" }}>
            <input 
              type="number" 
              placeholder="Payout Amount ($)" 
              value={payoutAmount} 
              onChange={e => setPayoutAmount(Number(e.target.value) || "")} 
              min={1} 
              className="input"
              style={{ flex: 1 }}
            />
            <button 
              className="btn primary"
              onClick={handlePayout} 
              disabled={!payoutAmount || !!(ledger && Number(payoutAmount) > ledger.balance)}
            >
              Request Payout
            </button>
          </div>
          
          <h3 className="muted" style={{ marginBottom: "16px" }}>Recent Transactions</h3>
          {(!ledger || ledger.transactions.length === 0) && <p className="muted">No transactions yet.</p>}
          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            {ledger?.transactions.slice(0, 5).map(tx => (
                <div key={tx.id} className="flex row space-between align-center" style={{ paddingBottom: "16px", borderBottom: "1px solid var(--border)" }}>
                    <div>
                        <div style={{ fontWeight: "bold" }}>{tx.type === 3 ? "Payout" : "Earnings"}</div>
                        <div className="muted">{dateString(tx.created_at)}</div>
                    </div>
                    <div style={{ fontWeight: "bold", color: tx.amount > 0 ? "#10b981" : "inherit" }}>
                        {tx.amount > 0 ? "+" : ""}{money(tx.amount)}
                    </div>
                </div>
            ))}
          </div>
        </div>

        {/* Assigned Ads */}
        <div className="card">
          <h2 style={{ marginBottom: "24px" }}>Direct Assignments</h2>
          {assignments.length === 0 ? (
            <p className="muted">No campaigns are explicitly assigned to your slots.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Campaign</th>
                  <th>Slot</th>
                  <th style={{ textAlign: "right" }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {assignments.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <div><b>{a.campaign_name}</b></div>
                      <div className="muted">{a.advertiser_name}</div>
                    </td>
                    <td>
                      {a.slot_name ? a.slot_name : "All slots"}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <Link
                        to={`/publisher/preview${a.slot_id ? `?slot_id=${encodeURIComponent(a.slot_id)}` : ""}`}
                        className="btn secondary small"
                      >
                        Preview
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
