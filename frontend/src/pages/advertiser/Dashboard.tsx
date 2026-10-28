import { useEffect, useState, useRef, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  listCampaigns,
  getAdvertiserLedger,
  getAdvertiserReporting,
  createAdvertiserPaymentRequest,
  listAdvertiserDomains,
  type LedgerResponse,
  type AdvertiserReporting,
  type PaymentOrderResponse,
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
  EmptyState,
} from "../../components/ui";
import StatCard from "../../components/StatCard";
import DateRangeSelector, { type DateRangeState } from "../../components/DateRangeSelector";
import TimeSeriesChart from "../../components/TimeSeriesChart";
import { ctr, money, num } from "../../lib/format";
import type { Campaign } from "../../types";

export default function AdvertiserDashboard() {
  const { session } = useAuth();
  const navigate = useNavigate();
  const [rows, setRows] = useState<Campaign[]>([]);
  const [ledger, setLedger] = useState<LedgerResponse | null>(null);
  const [reporting, setReporting] = useState<AdvertiserReporting | null>(null);
  const [domains, setDomains] = useState<AdvertiserDomain[]>([]);

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
      const [campaignsRes, ledgerRes, reportingRes, domainsRes] = await Promise.all([
        listCampaigns(session.user.advertiser_id),
        getAdvertiserLedger(session.user.advertiser_id),
        getAdvertiserReporting(session.user.advertiser_id, dateRange.startDate, dateRange.endDate),
        listAdvertiserDomains().catch(() => []),
      ]);
      setRows(campaignsRes);
      setLedger(ledgerRes);
      setReporting(reportingRes);
      setDomains(domainsRes);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not load dashboard data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, [session?.user.advertiser_id, dateRange]);

  const activeCount = rows.filter((c) => c.status === 1).length;
  const verifiedDomainsCount = domains.filter((d) => d.status === "VERIFIED").length;

  // Aggregate budget & pacing calculations
  const totalDailyCeiling = rows.reduce((sum, c) => sum + (c.status === 1 ? Number(c.budget_daily || 0) : 0), 0);
  const totalLifetimeBudget = rows.reduce((sum, c) => sum + Number(c.budget_total || 0), 0);
  const totalLifetimeSpent = rows.reduce((sum, c) => sum + Number(c.spent_total || 0), 0);
  const spentTodaySum = rows.reduce((sum, c) => sum + Number(c.spent_today || 0), 0);

  const dailyPacingPct = totalDailyCeiling > 0 ? Math.min(100, Math.round((spentTodaySum / totalDailyCeiling) * 100)) : 0;
  const lifetimePacingPct = totalLifetimeBudget > 0 ? Math.min(100, Math.round((totalLifetimeSpent / totalLifetimeBudget) * 100)) : 0;

  const stopPolling = useCallback(() => {
    if (pollingTimerRef.current) {
      clearInterval(pollingTimerRef.current);
      pollingTimerRef.current = null;
    }
  }, []);

  // Safe ledger check: queries authorized ledger endpoint
  const checkLedgerStatus = useCallback(
    async (orderId: string): Promise<boolean> => {
      if (!session?.user.advertiser_id || isCheckingRef.current) return false;
      isCheckingRef.current = true;
      try {
        const currentLedger = await getAdvertiserLedger(session.user.advertiser_id);
        setLedger(currentLedger);
        const isCredited = currentLedger.transactions.some((tx) => tx.reference_id === orderId);
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
    },
    [session?.user.advertiser_id, stopPolling]
  );

  // Polling for pending payments
  useEffect(() => {
    if (paymentState !== "pending" || !paymentOrder?.order_id) {
      stopPolling();
      return;
    }

    pollingAttemptsRef.current = 0;
    const maxAttempts = 30; // 90s timeout

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
    } catch (err: unknown) {
      setPaymentError(err instanceof Error ? err.message : "Status check failed");
    } finally {
      setIsCheckingManual(false);
    }
  };

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
    <PageContainer>
      <PageHeader
        title="Advertiser Dashboard"
        description="Monitor real-time delivery metrics, campaign pacing, budget allocations, and account liquidity."
        actions={
          <div style={{ display: "flex", gap: "var(--space-2)" }}>
            <Button variant="primary" onClick={() => navigate("/advertiser/campaigns/new")}>
              + New Campaign
            </Button>
            <Button variant="outline" onClick={() => navigate("/advertiser/campaign-builder")}>
              ✨ AI Campaign Assistant
            </Button>
          </div>
        }
      />

      {error && (
        <Alert variant="error" style={{ marginBottom: "var(--space-6)" }}>
          {error}
        </Alert>
      )}

      {/* Date Range Selector Header Toolbar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-6)" }}>
        <h2 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: 0 }}>
          Performance Overview
        </h2>
        <DateRangeSelector value={dateRange} onChange={setDateRange} />
      </div>

      {loading && !reporting ? (
        <div style={{ marginBottom: "var(--space-6)" }}>
          <LoadingSkeleton variant="rect" height={100} />
        </div>
      ) : reporting ? (
        <>
          <div className="grid cards" style={{ marginBottom: "var(--space-6)", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
            <StatCard title="Impressions" value={num(reporting.impressions)} />
            <StatCard title="Clicks" value={num(reporting.clicks)} />
            <StatCard title="CTR" value={`${Number(reporting.ctr).toFixed(2)}%`} />
            <StatCard title="Conversions" value={num(reporting.conversions)} />
            <StatCard title="Total Spend" value={money(reporting.spend)} />
          </div>

          {/* Time Series Chart Card */}
          <Card style={{ marginBottom: "var(--space-6)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-4)" }}>
              <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: 0 }}>
                Delivery Trend Analysis
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
        </>
      ) : null}

      {/* Main Grid: Campaigns Table (Left 2.5fr) and Wallet/Setup (Right 1fr) */}
      <div style={{ display: "grid", gridTemplateColumns: "2.5fr 1fr", gap: "var(--space-6)", alignItems: "start" }}>
        {/* Left Column: Campaigns & Pacing */}
        <div>
          {/* Pacing Overview Card */}
          {rows.length > 0 && (
            <Card style={{ marginBottom: "var(--space-6)" }}>
              <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-3) 0" }}>
                Budget Pacing Health
              </h3>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-4)" }}>
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--font-size-xs)" }}>
                    <span className="text-muted">Today's Spend Pacing</span>
                    <strong>{dailyPacingPct}% of ${totalDailyCeiling.toFixed(2)} daily limit</strong>
                  </div>
                  <div className="pacing-bar-container">
                    <div
                      className={`pacing-bar-fill ${dailyPacingPct > 90 ? "danger" : dailyPacingPct > 75 ? "warning" : ""}`}
                      style={{ width: `${dailyPacingPct}%` }}
                    />
                  </div>
                  <div className="text-muted" style={{ fontSize: "11px", marginTop: "var(--space-1)" }}>
                    Spent today: ${spentTodaySum.toFixed(2)}
                  </div>
                </div>

                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--font-size-xs)" }}>
                    <span className="text-muted">Lifetime Budget Pacing</span>
                    <strong>{lifetimePacingPct}% of ${totalLifetimeBudget.toFixed(2)} lifetime budget</strong>
                  </div>
                  <div className="pacing-bar-container">
                    <div
                      className={`pacing-bar-fill ${lifetimePacingPct > 95 ? "danger" : lifetimePacingPct > 80 ? "warning" : ""}`}
                      style={{ width: `${lifetimePacingPct}%` }}
                    />
                  </div>
                  <div className="text-muted" style={{ fontSize: "11px", marginTop: "var(--space-1)" }}>
                    Spent lifetime: ${totalLifetimeSpent.toFixed(2)}
                  </div>
                </div>
              </div>
            </Card>
          )}

          {/* Campaigns Performance Table */}
          <Card>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-4)" }}>
              <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: 0 }}>
                Campaigns ({rows.length})
              </h3>
              <Link to="/advertiser/campaigns/new" className="btn btn-outline btn-sm">
                + New Campaign
              </Link>
            </div>

            {loading && rows.length === 0 ? (
              <LoadingSkeleton variant="rect" height={200} />
            ) : rows.length === 0 ? (
              <EmptyState
                icon="📊"
                title="No campaigns found"
                description="Launch your first advertising campaign using our 3-step wizard or conversational assistant."
                action={
                  <div style={{ display: "flex", gap: "var(--space-2)", marginTop: "var(--space-3)" }}>
                    <Button variant="primary" onClick={() => navigate("/advertiser/campaigns/new")}>
                      Create Campaign (Wizard)
                    </Button>
                    <Button variant="outline" onClick={() => navigate("/advertiser/campaign-builder")}>
                      Use AI Assistant
                    </Button>
                  </div>
                }
              />
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table className="table" style={{ width: "100%", textAlign: "left" }}>
                  <thead>
                    <tr>
                      <th>Campaign Name</th>
                      <th>Status</th>
                      <th>Spent / Total Budget</th>
                      <th>Impressions</th>
                      <th>Clicks</th>
                      <th>CTR</th>
                      <th style={{ textAlign: "right" }}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((camp) => (
                      <tr key={camp.id}>
                        <td>
                          <Link
                            to={`/advertiser/campaigns/${camp.id}`}
                            style={{ fontWeight: "var(--font-weight-semibold)", color: "var(--color-primary)" }}
                          >
                            {camp.name}
                          </Link>
                          {camp.description && (
                            <div className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
                              {camp.description}
                            </div>
                          )}
                        </td>
                        <td>
                          <StatusBadge status={camp.status} />
                        </td>
                        <td>
                          {money(camp.spent_total)} / {money(camp.budget_total)}
                          <div className="text-muted" style={{ fontSize: "11px" }}>
                            Daily: ${camp.budget_daily}
                          </div>
                        </td>
                        <td>{num(camp.impressions)}</td>
                        <td>{num(camp.clicks)}</td>
                        <td>{ctr(camp.clicks, camp.impressions)}</td>
                        <td style={{ textAlign: "right" }}>
                          <Link to={`/advertiser/campaigns/${camp.id}`} className="btn btn-outline btn-sm">
                            Manage →
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>

        {/* Right Column: Wallet Liquidity & Onboarding Checklist */}
        <div>
          {/* Wallet Balance & Instant Top-Up Card */}
          <Card style={{ marginBottom: "var(--space-6)" }}>
            <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-2) 0" }}>
              Wallet Liquidity
            </h3>
            <div style={{ fontSize: "var(--font-size-3xl)", fontWeight: "var(--font-weight-bold)", color: "var(--color-text-primary)", marginBottom: "var(--space-1)" }}>
              {ledger ? money(ledger.balance) : "..."}
            </div>
            <div className="text-muted" style={{ fontSize: "var(--font-size-xs)", marginBottom: "var(--space-4)" }}>
              Active Campaigns: <strong>{activeCount}</strong> / {rows.length} Total
            </div>

            {/* Payment State: IDLE or CREATING */}
            {(paymentState === "idle" || paymentState === "creating") && (
              <div>
                <label className="form-label" style={{ fontSize: "var(--font-size-xs)" }}>
                  Instant Wallet Top-Up (Min ₹100 INR)
                </label>
                <div style={{ display: "flex", gap: "var(--space-2)", marginTop: "var(--space-1)" }}>
                  <input
                    type="number"
                    placeholder="Amount (₹)"
                    value={topUpAmount}
                    onChange={(e) => {
                      setTopUpAmount(Number(e.target.value) || "");
                      if (paymentError) setPaymentError(null);
                    }}
                    min={100}
                    disabled={paymentState === "creating"}
                    className="form-input"
                    style={{ flex: 1 }}
                  />
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={handleCreatePaymentRequest}
                    disabled={paymentState === "creating" || !topUpAmount || Number(topUpAmount) < 100}
                    isLoading={paymentState === "creating"}
                  >
                    Top-Up
                  </Button>
                </div>
                {paymentError && <p className="form-error" style={{ marginTop: "var(--space-1)" }}>{paymentError}</p>}
              </div>
            )}

            {/* Payment State: PENDING */}
            {paymentState === "pending" && paymentOrder && (
              <div style={{ marginTop: "var(--space-3)", padding: "var(--space-3)", border: "1px solid var(--color-border)", borderRadius: "var(--radius-lg)", background: "var(--color-bg-subtle)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-2)" }}>
                  <span style={{ fontSize: "var(--font-size-xs)", fontWeight: "bold", color: "var(--color-primary)" }}>PAYMENT PENDING</span>
                  <span style={{ fontWeight: "bold" }}>₹{Number(paymentOrder.amount).toFixed(2)} {paymentOrder.currency}</span>
                </div>

                {paymentOrder.qr_image_url ? (
                  <div style={{ textAlign: "center", margin: "var(--space-2) 0" }}>
                    <img
                      src={paymentOrder.qr_image_url}
                      alt="UPI QR"
                      style={{ maxWidth: "160px", width: "100%", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)" }}
                    />
                    <p style={{ fontSize: "11px", color: "var(--color-text-muted)", margin: "var(--space-1) 0 0 0" }}>
                      Scan with Google Pay, PhonePe, or any UPI app
                    </p>
                  </div>
                ) : (
                  <p className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
                    Please complete checkout for order {paymentOrder.order_id}.
                  </p>
                )}

                <div style={{ display: "flex", gap: "var(--space-2)", marginTop: "var(--space-3)" }}>
                  <Button variant="primary" size="sm" onClick={handleManualStatusCheck} isLoading={isCheckingManual} style={{ flex: 1 }}>
                    Check Status
                  </Button>
                  <Button variant="outline" size="sm" onClick={handleResetPayment}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}

            {/* Payment State: CONFIRMED */}
            {paymentState === "confirmed" && (
              <div style={{ marginTop: "var(--space-3)", padding: "var(--space-3)", border: "1px solid var(--color-success-border)", borderRadius: "var(--radius-lg)", background: "var(--color-success-subtle)" }}>
                <div style={{ color: "var(--color-success-text)", fontWeight: "bold", fontSize: "var(--font-size-sm)", marginBottom: "var(--space-1)" }}>
                  ✓ Deposit Confirmed!
                </div>
                <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-success-text)", margin: "0 0 var(--space-2) 0" }}>
                  Wallet credited with ₹{Number(paymentOrder?.amount || 0).toFixed(2)}.
                </p>
                <Button variant="outline" size="sm" onClick={handleResetPayment} style={{ width: "100%" }}>
                  Done
                </Button>
              </div>
            )}

            {/* Payment State: TIMEOUT */}
            {paymentState === "timeout" && (
              <div style={{ marginTop: "var(--space-3)", padding: "var(--space-3)", border: "1px solid var(--color-warning-border)", borderRadius: "var(--radius-lg)", background: "var(--color-warning-subtle)" }}>
                <div style={{ color: "var(--color-warning-text)", fontWeight: "bold", fontSize: "var(--font-size-xs)" }}>
                  ⏳ Verification in progress
                </div>
                <p style={{ fontSize: "11px", color: "var(--color-warning-text)", margin: "var(--space-1) 0 var(--space-2) 0" }}>
                  If you scanned the QR code, your deposit will be confirmed automatically once credited by the bank.
                </p>
                <div style={{ display: "flex", gap: "var(--space-2)" }}>
                  <Button variant="primary" size="sm" onClick={handleManualStatusCheck} isLoading={isCheckingManual} style={{ flex: 1 }}>
                    Check Again
                  </Button>
                  <Button variant="outline" size="sm" onClick={handleResetPayment}>
                    Close
                  </Button>
                </div>
              </div>
            )}
          </Card>

          {/* Setup & Activation Checklist */}
          <Card>
            <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-3) 0" }}>
              Advertiser Readiness
            </h3>

            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", fontSize: "var(--font-size-sm)" }}>
                <span style={{ color: (ledger?.balance ?? 0) > 0 ? "var(--color-success)" : "var(--color-error)", fontWeight: "bold" }}>
                  {(ledger?.balance ?? 0) > 0 ? "✓" : "✗"}
                </span>
                <span>Fund Wallet ({(ledger?.balance ?? 0) > 0 ? "Ready" : "Balance ₹0"})</span>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", fontSize: "var(--font-size-sm)" }}>
                <span style={{ color: verifiedDomainsCount > 0 ? "var(--color-success)" : "var(--color-warning)", fontWeight: "bold" }}>
                  {verifiedDomainsCount > 0 ? "✓" : "⚠"}
                </span>
                <div>
                  <span>Verify Landing Domains ({verifiedDomainsCount} verified)</span>
                  {verifiedDomainsCount === 0 && (
                    <div>
                      <Link to="/advertiser/domains" style={{ fontSize: "11px", color: "var(--color-primary)" }}>
                        Register Domain →
                      </Link>
                    </div>
                  )}
                </div>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", fontSize: "var(--font-size-sm)" }}>
                <span style={{ color: activeCount > 0 ? "var(--color-success)" : "var(--color-text-muted)", fontWeight: "bold" }}>
                  {activeCount > 0 ? "✓" : "○"}
                </span>
                <span>Active Campaign Serving ({activeCount} live)</span>
              </div>
            </div>
          </Card>
        </div>
      </div>
    </PageContainer>
  );
}
