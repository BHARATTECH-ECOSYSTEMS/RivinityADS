import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { 
  getPublisherLedger, 
  requestPayout, 
  getPublisherReporting, 
  listPublisherAssignments, 
  getPlacementHealth,
  listPublisherProperties,
  listSlots,
  type LedgerResponse, 
  type PublisherReporting, 
  type PublisherAssignment,
  type PlacementHealthResponse,
  type PublisherProperty,
  type SlotData,
} from "../../api/client";
import { 
  PageContainer, 
  PageHeader, 
  Card, 
  Button, 
  StatusBadge, 
  Alert, 
  LoadingSkeleton, 
  EmptyState 
} from "../../components/ui";
import StatCard from "../../components/StatCard";
import DateRangeSelector, { type DateRangeState } from "../../components/DateRangeSelector";
import TimeSeriesChart from "../../components/TimeSeriesChart";
import { money, dateString, num } from "../../lib/format";

export default function PublisherDashboard() {
  const { session } = useAuth();
  
  const [reporting, setReporting] = useState<PublisherReporting | null>(null);
  const [ledger, setLedger] = useState<LedgerResponse | null>(null);
  const [assignments, setAssignments] = useState<PublisherAssignment[]>([]);
  const [placementHealth, setPlacementHealth] = useState<PlacementHealthResponse | null>(null);
  const [properties, setProperties] = useState<PublisherProperty[]>([]);
  const [slots, setSlots] = useState<SlotData[]>([]);
  
  const [payoutAmount, setPayoutAmount] = useState<number | "">("");
  const [payoutSubmitting, setPayoutSubmitting] = useState(false);
  const [payoutFeedback, setPayoutFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);
  
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
      const [reportData, propsData, slotsData] = await Promise.all([
        getPublisherReporting(publisherId, dateRange.startDate, dateRange.endDate),
        listPublisherProperties(publisherId).catch(() => []),
        listSlots(publisherId).catch(() => []),
      ]);
      setReporting(reportData);
      setProperties(propsData);
      setSlots(slotsData);

      // Fetch non-blocking data
      getPublisherLedger(publisherId).then(setLedger).catch((err) => {
        console.error("Ledger fetch failed:", err);
      });

      listPublisherAssignments().then(setAssignments).catch((err) => {
        console.error("Assignments fetch failed:", err);
      });

      getPlacementHealth(publisherId, 30).then(setPlacementHealth).catch((err) => {
        console.error("Placement health fetch failed:", err);
      });
      
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { 
    void reload(); 
  }, [publisherId, dateRange]);

  const handlePayout = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!payoutAmount || payoutAmount <= 0 || !publisherId) return;
    if (ledger && Number(payoutAmount) > ledger.balance) {
      setPayoutFeedback({ type: "error", message: "Payout amount exceeds available ledger balance." });
      return;
    }
    
    setPayoutSubmitting(true);
    setPayoutFeedback(null);
    try {
      await requestPayout(publisherId, Number(payoutAmount), `payout_${Date.now()}`);
      setPayoutAmount("");
      setPayoutFeedback({ type: "success", message: "Payout request submitted successfully and recorded in ledger." });
      // Refresh ledger
      const updatedLedger = await getPublisherLedger(publisherId);
      setLedger(updatedLedger);
    } catch (err: any) {
      setPayoutFeedback({ type: "error", message: err.message || "Payout request failed" });
    } finally {
      setPayoutSubmitting(false);
    }
  };

  // Onboarding Step Calculations
  const emailVerified = !!session?.user.email_verified;
  const hasProperties = properties.length > 0;
  const hasSlots = slots.length > 0;
  const hasImpressions = (reporting?.impressions ?? 0) > 0;

  const onboardingSteps = [
    {
      id: "email",
      label: "Verify email address",
      completed: emailVerified,
      hint: emailVerified ? "Verified" : "Check your inbox to verify your email",
      action: !emailVerified ? { label: "Verify", href: "/verify-email" } : undefined,
    },
    {
      id: "property",
      label: "Register website or app property",
      completed: hasProperties,
      hint: hasProperties ? `${properties.length} property registered` : "Add your site domain",
      action: !hasProperties ? { label: "Add Property", href: "/publisher/properties" } : undefined,
    },
    {
      id: "slot",
      label: "Create an ad placement slot",
      completed: hasSlots,
      hint: hasSlots ? `${slots.length} ad units configured` : "Define banner or native slots",
      action: !hasSlots ? { label: "Create Slot", href: "/publisher/slots" } : undefined,
    },
    {
      id: "traffic",
      label: "Serve first live ad impression",
      completed: hasImpressions,
      hint: hasImpressions ? "Traffic verified and serving" : "Embed ad tag to start serving",
      action: !hasImpressions ? { label: "Get Tag", href: "/publisher/integration" } : undefined,
    },
  ];

  const completedStepsCount = onboardingSteps.filter((s) => s.completed).length;
  const allStepsComplete = completedStepsCount === onboardingSteps.length;

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Publisher Operations"
        title="Publisher Dashboard"
        description="Monitor your inventory performance, track real-time earnings, and verify integration health."
        actions={
          <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
            <Link to="/publisher/preview" className="btn btn-secondary btn-sm">
              Live Preview
            </Link>
            <DateRangeSelector value={dateRange} onChange={setDateRange} />
          </div>
        }
      />

      {error && (
        <Alert variant="danger" title="Error Loading Dashboard" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Publisher Onboarding Progress Banner */}
      <Card
        className="onboarding-progress-card"
        title="Publisher Onboarding & Setup Status"
        subtitle={
          allStepsComplete
            ? "Your inventory is fully configured, verified, and actively serving ads."
            : `${completedStepsCount} of ${onboardingSteps.length} onboarding requirements completed.`
        }
        footer={
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
            <span style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
              {allStepsComplete ? "All pre-requisites satisfied." : "Complete remaining setup items to maximize fill rate."}
            </span>
            <Link to="/publisher/integration" className="btn btn-ghost btn-sm">
              View Integration Snippets →
            </Link>
          </div>
        }
      >
        <div style={{ marginBottom: "16px" }}>
          <div
            style={{
              height: "8px",
              width: "100%",
              backgroundColor: "var(--color-bg-subtle)",
              borderRadius: "var(--radius-full)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                height: "100%",
                width: `${(completedStepsCount / onboardingSteps.length) * 100}%`,
                backgroundColor: allStepsComplete ? "var(--color-success)" : "var(--color-primary)",
                transition: "width 0.3s ease",
              }}
            />
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "12px" }}>
          {onboardingSteps.map((step, idx) => (
            <div
              key={step.id}
              style={{
                padding: "12px",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--border-color)",
                backgroundColor: step.completed ? "var(--color-bg-surface)" : "var(--color-bg-subtle)",
                display: "flex",
                flexDirection: "column",
                gap: "6px",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "0.75rem", fontWeight: 700, color: "var(--color-text-muted)" }}>
                  STEP {idx + 1}
                </span>
                <StatusBadge 
                  status={step.completed ? "active" : "pending"} 
                  label={step.completed ? "Complete" : "Action Needed"}
                  size="sm"
                />
              </div>
              <div style={{ fontWeight: 600, fontSize: "0.875rem", color: "var(--color-text-primary)" }}>
                {step.label}
              </div>
              <div style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)" }}>
                {step.hint}
              </div>
              {step.action && (
                <div style={{ marginTop: "4px" }}>
                  <Link to={step.action.href} className="btn btn-secondary btn-sm" style={{ width: "100%", textAlign: "center" }}>
                    {step.action.label}
                  </Link>
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>

      {/* Key Metrics */}
      {loading && !reporting ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "16px", marginBottom: "24px" }}>
          <LoadingSkeleton variant="rect" height={100} />
          <LoadingSkeleton variant="rect" height={100} />
          <LoadingSkeleton variant="rect" height={100} />
          <LoadingSkeleton variant="rect" height={100} />
        </div>
      ) : reporting ? (
        <>
          <div className="grid cards" style={{ marginBottom: "24px", gridTemplateColumns: "repeat(4, 1fr)" }}>
            <StatCard title="Impressions" value={num(reporting.impressions)} />
            <StatCard title="Clicks" value={num(reporting.clicks)} />
            <StatCard title="Average eCPM" value={money(reporting.ecpm)} />
            <StatCard title="Period Earnings" value={money(reporting.earnings)} />
          </div>

          {/* Performance Chart */}
          <Card className="chart-card" style={{ marginBottom: "24px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
              <div style={{ fontWeight: 600, fontSize: "1rem" }}>Daily Delivery Breakdown</div>
              <div style={{ display: "flex", gap: "8px" }}>
                {(["impressions", "clicks", "earnings", "conversions"] as const).map((metric) => (
                  <button 
                    key={metric}
                    type="button"
                    className={`btn ${activeTab === metric ? "btn-primary" : "btn-secondary"} btn-sm`}
                    onClick={() => setActiveTab(metric)}
                    style={{ textTransform: "capitalize" }}
                  >
                    {metric}
                  </button>
                ))}
              </div>
            </div>
            
            <TimeSeriesChart 
              data={reporting.daily_breakdown} 
              metric={activeTab} 
              color={
                activeTab === "impressions" ? "#2563eb" : 
                activeTab === "clicks" ? "#10b981" : 
                activeTab === "earnings" ? "#f59e0b" : "#8b5cf6"
              } 
            />
          </Card>
        </>
      ) : null}

      {/* Placement Health Summary Section */}
      <Card
        title="Placement Health & Benchmarks (Rolling 30 Days)"
        subtitle="Automated performance classification and heuristic diagnostics for your ad units."
        footer={
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
            <span style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
              Units with insufficient volume (&lt;1,000 imps) are categorized as Insufficient Data.
            </span>
            <Link to="/publisher/health" className="btn btn-secondary btn-sm">
              View All Placements &amp; Benchmarks →
            </Link>
          </div>
        }
        style={{ marginBottom: "24px" }}
      >
        {!placementHealth ? (
          <div style={{ padding: "20px" }}>
            <LoadingSkeleton variant="rect" height={120} />
          </div>
        ) : placementHealth.placements.length === 0 ? (
          <EmptyState
            title="No ad placements recorded"
            description="Create ad units and serve traffic to receive placement health diagnostics and format benchmarking."
            action={
              <Link to="/publisher/slots" className="btn btn-primary btn-sm">
                Create Ad Unit
              </Link>
            }
          />
        ) : (
          <div className="table-container" style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Placement</th>
                  <th>Format</th>
                  <th style={{ textAlign: "right" }}>30d Volume</th>
                  <th style={{ textAlign: "right" }}>CTR</th>
                  <th style={{ textAlign: "right" }}>eCPM</th>
                  <th>Verdict</th>
                  <th>Recommendation</th>
                </tr>
              </thead>
              <tbody>
                {placementHealth.placements.slice(0, 5).map((p) => {
                  let badgeStatus: "success" | "active" | "warning" | "pending" = "pending";
                  let badgeLabel = "Insufficient Data";
                  if (p.verdict === "great") {
                    badgeStatus = "success";
                    badgeLabel = "★ Great";
                  } else if (p.verdict === "healthy") {
                    badgeStatus = "active";
                    badgeLabel = "✓ Healthy";
                  } else if (p.verdict === "low_visibility") {
                    badgeStatus = "warning";
                    badgeLabel = "⚠ Low Visibility";
                  }

                  return (
                    <tr key={p.slot_id}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{p.slot_name}</div>
                        <div style={{ fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--color-text-muted)" }}>
                          {p.slot_id}
                        </div>
                      </td>
                      <td>
                        <span style={{ fontSize: "11px", background: "var(--color-bg-subtle)", padding: "2px 6px", borderRadius: "4px", textTransform: "uppercase", fontWeight: 600 }}>
                          {p.format_name}
                        </span>
                      </td>
                      <td style={{ textAlign: "right" }}>{num(p.impressions)}</td>
                      <td style={{ textAlign: "right" }}>{(p.ctr * 100).toFixed(2)}%</td>
                      <td style={{ textAlign: "right", fontWeight: 600 }}>{money(p.ecpm)}</td>
                      <td>
                        <StatusBadge status={badgeStatus} label={badgeLabel} size="sm" />
                      </td>
                      <td style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)" }}>
                        {p.recommendation}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "24px", alignItems: "start" }}>
        
        {/* Earnings & Wallet */}
        <Card
          title="Earnings & Payout Wallet"
          subtitle="Real-time publisher revenue ledger and verified payout requests."
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px", padding: "16px", background: "var(--color-bg-subtle)", borderRadius: "var(--radius-lg)" }}>
            <div>
              <div style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Available Ledger Balance</div>
              <div style={{ fontSize: "1.75rem", fontWeight: 700, color: "var(--color-success)" }}>
                {ledger ? money(ledger.balance) : "..."}
              </div>
            </div>
            <StatusBadge status="active" label="Settled & Audited" size="sm" />
          </div>

          {payoutFeedback && (
            <Alert 
              variant={payoutFeedback.type === "success" ? "success" : "danger"} 
              onClose={() => setPayoutFeedback(null)}
            >
              {payoutFeedback.message}
            </Alert>
          )}

          <form onSubmit={handlePayout} style={{ display: "flex", gap: "8px", alignItems: "center", marginBottom: "24px" }}>
            <input 
              type="number" 
              placeholder="Payout Amount ($)" 
              value={payoutAmount} 
              onChange={(e) => setPayoutAmount(e.target.value === "" ? "" : Number(e.target.value))} 
              min={1} 
              step="any"
              max={ledger ? ledger.balance : undefined}
              className="input"
              style={{ flex: 1 }}
              required
            />
            <Button 
              type="submit" 
              variant="primary" 
              isLoading={payoutSubmitting}
              disabled={!payoutAmount || Number(payoutAmount) <= 0 || !!(ledger && Number(payoutAmount) > ledger.balance)}
            >
              Request Payout
            </Button>
          </form>

          <h3 style={{ fontSize: "0.9375rem", fontWeight: 600, marginBottom: "12px", color: "var(--color-text-secondary)" }}>
            Recent Transactions
          </h3>
          {(!ledger || ledger.transactions.length === 0) ? (
            <p style={{ color: "var(--color-text-muted)", fontSize: "0.875rem" }}>No ledger transactions recorded yet.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              {ledger.transactions.slice(0, 5).map((tx) => (
                <div 
                  key={tx.id} 
                  style={{ 
                    display: "flex", 
                    justifyContent: "space-between", 
                    alignItems: "center", 
                    padding: "8px 0", 
                    borderBottom: "1px solid var(--border-color)" 
                  }}
                >
                  <div>
                    <div style={{ fontWeight: 600, fontSize: "0.875rem" }}>
                      {tx.type === 3 ? "Payout Disbursed" : "Ad Serving Earnings"}
                    </div>
                    <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                      {dateString(tx.created_at)} {tx.reference_id ? `· ${tx.reference_id}` : ""}
                    </div>
                  </div>
                  <div style={{ fontWeight: 700, fontSize: "0.9375rem", color: tx.amount > 0 ? "var(--color-success)" : "inherit" }}>
                    {tx.amount > 0 ? "+" : ""}{money(tx.amount)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* Direct Assignments */}
        <Card
          title="Direct Campaign Assignments"
          subtitle="Campaigns explicitly assigned to your publisher ad inventory."
        >
          {assignments.length === 0 ? (
            <EmptyState
              title="No direct campaign assignments"
              description="No campaigns are currently locked exclusively to your ad slots. Standard matching applies."
            />
          ) : (
            <div className="table-container" style={{ overflowX: "auto" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Campaign</th>
                    <th>Target Slot</th>
                    <th style={{ textAlign: "right" }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {assignments.map((a) => (
                    <tr key={a.id}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{a.campaign_name}</div>
                        <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>{a.advertiser_name}</div>
                      </td>
                      <td>
                        {a.slot_name ? a.slot_name : <span style={{ color: "var(--color-text-muted)" }}>All slots</span>}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <Link
                          to={`/publisher/preview${a.slot_id ? `?slot_id=${encodeURIComponent(a.slot_id)}` : ""}`}
                          className="btn btn-secondary btn-sm"
                        >
                          Preview
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
    </PageContainer>
  );
}
