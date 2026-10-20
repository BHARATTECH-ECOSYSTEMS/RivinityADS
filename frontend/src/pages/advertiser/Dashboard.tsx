import { useEffect, useState, useRef, useCallback } from "react";
import { Link } from "react-router-dom";
import { 
    listCampaigns, 
    getAdvertiserLedger, 
    getAdvertiserReporting,
    createAdvertiserPaymentRequest,
    type LedgerResponse,
    type AdvertiserReporting,
    type PaymentOrderResponse
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

    // Wallet top-up state
    const [topUpAmount, setTopUpAmount] = useState<number | "">("");
    const [paymentState, setPaymentState] = useState<"idle" | "creating" | "pending" | "confirmed" | "timeout" | "failed">("idle");
    const [paymentOrder, setPaymentOrder] = useState<PaymentOrderResponse | null>(null);
    const [paymentError, setPaymentError] = useState<string | null>(null);
    const [isCheckingManual, setIsCheckingManual] = useState(false);

    const pollingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const pollingAttemptsRef = useRef(0);
    const isCheckingRef = useRef(false);

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

    const stopPolling = useCallback(() => {
        if (pollingTimerRef.current) {
            clearInterval(pollingTimerRef.current);
            pollingTimerRef.current = null;
        }
    }, []);

    // Safe ledger check: queries the authorized ledger endpoint; never modifies funds on client
    const checkLedgerStatus = useCallback(async (orderId: string): Promise<boolean> => {
        if (!session?.user.advertiser_id || isCheckingRef.current) return false;
        isCheckingRef.current = true;
        try {
            const currentLedger = await getAdvertiserLedger(session.user.advertiser_id);
            setLedger(currentLedger);
            const isCredited = currentLedger.transactions.some(tx => tx.reference_id === orderId);
            if (isCredited) {
                setPaymentState("confirmed");
                stopPolling();
                return true;
            }
            return false;
        } catch {
            return false;
        } finally {
            isCheckingRef.current = false;
        }
    }, [session?.user.advertiser_id, stopPolling]);

    // Bounded polling effect for pending payment
    useEffect(() => {
        if (paymentState !== "pending" || !paymentOrder?.order_id) {
            stopPolling();
            return;
        }

        pollingAttemptsRef.current = 0;
        const maxAttempts = 30; // 30 attempts * 3s = 90s bounded timeout

        const poll = async () => {
            const confirmed = await checkLedgerStatus(paymentOrder.order_id);
            if (!confirmed) {
                pollingAttemptsRef.current += 1;
                if (pollingAttemptsRef.current >= maxAttempts) {
                    stopPolling();
                    setPaymentState("timeout");
                }
            }
        };

        pollingTimerRef.current = setInterval(poll, 3000);

        return () => {
            stopPolling();
        };
    }, [paymentState, paymentOrder?.order_id, checkLedgerStatus, stopPolling]);

    // Safe manual status check
    const handleManualStatusCheck = async () => {
        if (!paymentOrder?.order_id || isCheckingManual) return;
        setIsCheckingManual(true);
        setPaymentError(null);
        try {
            const confirmed = await checkLedgerStatus(paymentOrder.order_id);
            if (!confirmed && paymentState === "timeout") {
                pollingAttemptsRef.current = 0;
                setPaymentState("pending");
            }
        } catch (err) {
            setPaymentError(err instanceof Error ? err.message : "Status check failed");
        } finally {
            setIsCheckingManual(false);
        }
    };

    // Initiate self-service payment request
    const handleCreatePaymentRequest = async () => {
        if (paymentState === "creating") return;
        if (!topUpAmount || Number(topUpAmount) < 100) {
            setPaymentError("Minimum top-up amount is ₹100");
            return;
        }
        if (!session?.user.advertiser_id) return;

        setPaymentState("creating");
        setPaymentError(null);
        try {
            const res = await createAdvertiserPaymentRequest(session.user.advertiser_id, Number(topUpAmount));
            setPaymentOrder(res);
            setPaymentState("pending");
        } catch (err: unknown) {
            setPaymentError(err instanceof Error ? err.message : "Failed to create payment request");
            setPaymentState("failed");
        }
    };

    const handleResetPayment = () => {
        stopPolling();
        setPaymentState("idle");
        setPaymentOrder(null);
        setPaymentError(null);
        setTopUpAmount("");
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

                        {/* Payment state: IDLE or CREATING */}
                        {(paymentState === "idle" || paymentState === "creating") && (
                            <div>
                                <label style={{ fontSize: "12px", color: "#64748b", marginBottom: "4px" }}>
                                    Top-Up Wallet (Min ₹100 INR)
                                </label>
                                <div className="flex row gap" style={{ alignItems: "center", marginBottom: "8px" }}>
                                    <input
                                        type="number"
                                        placeholder="Amount (₹)"
                                        value={topUpAmount}
                                        onChange={e => {
                                            setTopUpAmount(Number(e.target.value) || "");
                                            if (paymentError) setPaymentError(null);
                                        }}
                                        min={100}
                                        disabled={paymentState === "creating"}
                                        style={{ flex: 1, padding: "8px", border: "1px solid #cbd5e1", borderRadius: "4px" }}
                                    />
                                    <button
                                        className="btn primary"
                                        onClick={handleCreatePaymentRequest}
                                        disabled={paymentState === "creating" || !topUpAmount || Number(topUpAmount) < 100}
                                    >
                                        {paymentState === "creating" ? "Creating..." : "Top-Up"}
                                    </button>
                                </div>
                                {paymentError && (
                                    <div style={{ color: "#ef4444", fontSize: "12px", marginTop: "4px" }}>
                                        {paymentError}
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Payment state: PENDING */}
                        {paymentState === "pending" && paymentOrder && (
                            <div style={{ marginTop: "12px", padding: "12px", border: "1px solid #cbd5e1", borderRadius: "8px", backgroundColor: "#f8fafc" }}>
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                                    <span style={{ fontSize: "12px", fontWeight: 600, color: "#2563eb" }}>PAYMENT PENDING</span>
                                    <span style={{ fontSize: "14px", fontWeight: "bold" }}>
                                        ₹{Number(paymentOrder.amount).toFixed(2)} {paymentOrder.currency}
                                    </span>
                                </div>
                                <div style={{ fontSize: "11px", color: "#64748b", marginBottom: "8px", wordBreak: "break-all" }}>
                                    Order: {paymentOrder.order_id}
                                </div>
                                {paymentOrder.qr_image_url ? (
                                    <div style={{ textAlign: "center", margin: "10px 0" }}>
                                        <img
                                            src={paymentOrder.qr_image_url}
                                            alt="Payment QR Code"
                                            style={{ maxWidth: "180px", width: "100%", borderRadius: "8px", border: "1px solid #e2e8f0" }}
                                        />
                                        <p style={{ fontSize: "12px", color: "#475569", margin: "6px 0 0" }}>
                                            Scan with any UPI app to complete payment
                                        </p>
                                    </div>
                                ) : (
                                    <p style={{ fontSize: "12px", color: "#475569", margin: "8px 0" }}>
                                        Please complete payment for order {paymentOrder.order_id}.
                                    </p>
                                )}
                                <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "#0284c7", margin: "10px 0" }}>
                                    <span className="dot ok" style={{ margin: 0, animation: "pulse 1.5s infinite" }} />
                                    <span>Waiting for payment confirmation...</span>
                                </div>
                                {paymentError && (
                                    <div style={{ color: "#ef4444", fontSize: "12px", marginBottom: "8px" }}>
                                        {paymentError}
                                    </div>
                                )}
                                <div className="flex row gap" style={{ marginTop: "10px" }}>
                                    <button
                                        className="btn primary small"
                                        onClick={handleManualStatusCheck}
                                        disabled={isCheckingManual}
                                        style={{ flex: 1 }}
                                    >
                                        {isCheckingManual ? "Checking..." : "I Have Paid / Check"}
                                    </button>
                                    <button
                                        className="btn secondary small"
                                        onClick={handleResetPayment}
                                    >
                                        Cancel
                                    </button>
                                </div>
                            </div>
                        )}

                        {/* Payment state: CONFIRMED */}
                        {paymentState === "confirmed" && (
                            <div style={{ marginTop: "12px", padding: "12px", border: "1px solid #86efac", borderRadius: "8px", backgroundColor: "#f0fdf4" }}>
                                <div style={{ color: "#16a34a", fontWeight: "bold", fontSize: "14px", marginBottom: "4px" }}>
                                    ✓ Payment Confirmed!
                                </div>
                                <p style={{ fontSize: "12px", color: "#15803d", margin: "0 0 10px" }}>
                                    Your wallet has been credited with ₹{Number(paymentOrder?.amount || 0).toFixed(2)}.
                                </p>
                                <button className="btn primary small" onClick={handleResetPayment} style={{ width: "100%" }}>
                                    Done
                                </button>
                            </div>
                        )}

                        {/* Payment state: TIMEOUT */}
                        {paymentState === "timeout" && (
                            <div style={{ marginTop: "12px", padding: "12px", border: "1px solid #fde047", borderRadius: "8px", backgroundColor: "#fefce8" }}>
                                <div style={{ color: "#ca8a04", fontWeight: "bold", fontSize: "13px", marginBottom: "4px" }}>
                                    ⏳ Verification taking longer
                                </div>
                                <p style={{ fontSize: "12px", color: "#713f12", margin: "0 0 10px" }}>
                                    If you have completed the UPI payment, your wallet will credit automatically once confirmed by the bank.
                                </p>
                                <div className="flex row gap">
                                    <button
                                        className="btn primary small"
                                        onClick={handleManualStatusCheck}
                                        disabled={isCheckingManual}
                                        style={{ flex: 1 }}
                                    >
                                        {isCheckingManual ? "Checking..." : "Check Status"}
                                    </button>
                                    <button className="btn secondary small" onClick={handleResetPayment}>
                                        Close
                                    </button>
                                </div>
                            </div>
                        )}

                        {/* Payment state: FAILED */}
                        {paymentState === "failed" && (
                            <div style={{ marginTop: "12px", padding: "12px", border: "1px solid #fca5a5", borderRadius: "8px", backgroundColor: "#fef2f2" }}>
                                <div style={{ color: "#dc2626", fontWeight: "bold", fontSize: "13px", marginBottom: "4px" }}>
                                    ✕ Top-Up Request Failed
                                </div>
                                <p style={{ fontSize: "12px", color: "#991b1b", margin: "0 0 10px" }}>
                                    {paymentError || "Could not create payment order. Please try again."}
                                </p>
                                <button className="btn primary small" onClick={() => setPaymentState("idle")} style={{ width: "100%" }}>
                                    Try Again
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
