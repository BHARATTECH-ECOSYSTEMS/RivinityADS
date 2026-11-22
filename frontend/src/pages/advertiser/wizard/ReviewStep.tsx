import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  activateCampaign,
  getCampaignReadiness,
  getAdvertiserLedger,
  listAdvertiserDomains,
  createAdvertiserPaymentRequest,
  type CampaignReadinessResponse,
  type LedgerResponse,
  type AdvertiserDomain,
  type PaymentOrderResponse,
} from "../../../api/client";
import { useAuth } from "../../../auth/AuthContext";
import { BID_TYPES, type Campaign, type AdSet, type Creative } from "../../../types";
import { money } from "../../../lib/format";
import { Button, Alert, Card, StatusBadge, Input, LoadingSkeleton } from "../../../components/ui";

interface ReviewStepProps {
  campaign: Campaign;
  adSets: AdSet[];
  creatives: Creative[];
  onBack: () => void;
  onActivated: (updated: Campaign) => void;
}

export function ReviewStep({
  campaign,
  adSets,
  creatives,
  onBack,
  onActivated,
}: ReviewStepProps) {
  const { session } = useAuth();
  const navigate = useNavigate();

  // Server-authoritative readiness state
  const [readiness, setReadiness] = useState<CampaignReadinessResponse | null>(null);
  const [readinessLoading, setReadinessLoading] = useState(true);
  const [readinessError, setReadinessError] = useState<string | null>(null);

  // Auxiliary context states
  const [ledger, setLedger] = useState<LedgerResponse | null>(null);
  const [ledgerLoading, setLedgerLoading] = useState(true);
  const [ledgerError, setLedgerError] = useState<string | null>(null);

  const [domains, setDomains] = useState<AdvertiserDomain[]>([]);
  const [domainsLoading, setDomainsLoading] = useState(true);
  const [domainsError, setDomainsError] = useState<string | null>(null);

  // Activation submission state
  const [submitting, setSubmitting] = useState(false);
  const [activationError, setActivationError] = useState<string | null>(null);
  const [activationSuccess, setActivationSuccess] = useState(false);

  // Quick wallet top-up state
  const [topUpAmount, setTopUpAmount] = useState<string>("500");
  const [topUpLoading, setTopUpLoading] = useState(false);
  const [topUpOrder, setTopUpOrder] = useState<PaymentOrderResponse | null>(null);
  const [topUpError, setTopUpError] = useState<string | null>(null);

  const fetchReadiness = async () => {
    setReadinessLoading(true);
    setReadinessError(null);
    try {
      const data = await getCampaignReadiness(campaign.id);
      setReadiness(data);
    } catch (err: unknown) {
      setReadinessError(err instanceof Error ? err.message : "Failed to load authoritative readiness state");
    } finally {
      setReadinessLoading(false);
    }
  };

  const fetchLedger = async () => {
    if (!session?.user.advertiser_id) return;
    setLedgerLoading(true);
    setLedgerError(null);
    try {
      const data = await getAdvertiserLedger(session.user.advertiser_id);
      setLedger(data);
    } catch (err: unknown) {
      setLedgerError(err instanceof Error ? err.message : "Failed to load advertiser wallet ledger");
    } finally {
      setLedgerLoading(false);
    }
  };

  const fetchDomains = async () => {
    setDomainsLoading(true);
    setDomainsError(null);
    try {
      const data = await listAdvertiserDomains();
      setDomains(data);
    } catch (err: unknown) {
      setDomainsError(err instanceof Error ? err.message : "Failed to load verified domain list");
    } finally {
      setDomainsLoading(false);
    }
  };

  useEffect(() => {
    void fetchReadiness();
    void fetchLedger();
    void fetchDomains();
  }, [campaign.id, session?.user.advertiser_id]);

  const balance = readiness?.summary?.wallet_balance ?? ledger?.balance ?? 0;

  // Authoritative checklist derivation
  const hasBudget = (readiness?.summary?.budget_daily ?? campaign.budget_daily) > 0 &&
    (readiness?.summary?.budget_total ?? campaign.budget_total) >= (readiness?.summary?.budget_daily ?? campaign.budget_daily);
  
  const totalAdSetsCount = readiness?.summary?.total_ad_sets ?? adSets.length;
  const activeAdSetsCount = readiness?.summary?.active_ad_sets ?? adSets.filter((a) => a.status === 1).length;
  const hasAdSets = totalAdSetsCount > 0;
  const hasActiveAdSets = activeAdSetsCount > 0;

  const totalCreativesCount = readiness?.summary?.total_creatives ?? creatives.length;
  const activeCreativesCount = readiness?.summary?.active_creatives ?? creatives.filter((c) => c.status === 1).length;
  const hasCreatives = totalCreativesCount > 0;
  const hasActiveCreatives = activeCreativesCount > 0;

  const approvedCreativesCount = readiness?.summary?.approved_creatives ?? creatives.filter((c) => c.moderation_status === 2).length;
  const unapprovedCount = readiness?.summary?.unapproved_creatives ?? (totalCreativesCount - approvedCreativesCount);

  const verifiedCreativesCount = readiness?.summary?.verified_creatives ?? 0;
  const unverifiedCount = readiness?.summary?.unverified_creatives ?? (totalCreativesCount - verifiedCreativesCount);

  const hasTargeting = readiness?.summary?.has_targeting ?? true;
  const hasPositiveBalance = balance > 0;

  // Server authoritative readiness flag
  const blockers = readiness?.blockers ?? [];
  const warnings = readiness?.warnings ?? [];
  const isServerReady = readiness?.is_ready ?? false;

  const canAttemptActivation =
    isServerReady &&
    blockers.length === 0 &&
    unapprovedCount === 0 &&
    unverifiedCount === 0 &&
    campaign.status === 5 && // DRAFT
    !readinessLoading &&
    !submitting;

  const handleActivate = async () => {
    if (submitting || !canAttemptActivation) return;
    setSubmitting(true);
    setActivationError(null);

    try {
      const result = await activateCampaign(campaign.id);
      if (result.status === 1) {
        setActivationSuccess(true);
        onActivated(result);
      } else {
        setActivationError("Server responded, but campaign status was not set to ACTIVE.");
      }
    } catch (err: unknown) {
      setActivationError(err instanceof Error ? err.message : "Failed to activate campaign");
      // Refresh readiness state to pull updated server blockers
      void fetchReadiness();
    } finally {
      setSubmitting(false);
    }
  };

  const handleQuickTopUp = async () => {
    if (!session?.user.advertiser_id) return;
    const amountNum = Number(topUpAmount);
    if (isNaN(amountNum) || amountNum < 100) {
      setTopUpError("Minimum deposit is ₹100");
      return;
    }

    setTopUpLoading(true);
    setTopUpError(null);
    try {
      const order = await createAdvertiserPaymentRequest(session.user.advertiser_id, amountNum);
      setTopUpOrder(order);
    } catch (err: unknown) {
      setTopUpError(err instanceof Error ? err.message : "Could not create payment request");
    } finally {
      setTopUpLoading(false);
    }
  };

  return (
    <div>
      <Card style={{ marginBottom: "var(--space-6)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "var(--space-4)" }}>
          <div>
            <h2 style={{ fontSize: "var(--font-size-xl)", fontWeight: "var(--font-weight-bold)", margin: "0 0 var(--space-1) 0" }}>
              Step 4: Campaign Review &amp; Activation
            </h2>
            <p className="text-muted" style={{ margin: 0 }}>
              Verify server-authoritative readiness, review hierarchical spend allocations, and launch into active ad delivery.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void fetchReadiness();
              void fetchLedger();
              void fetchDomains();
            }}
            disabled={readinessLoading}
          >
            {readinessLoading ? "Checking..." : "🔄 Re-check Readiness"}
          </Button>
        </div>

        {/* Data Loading and Network Error Banners */}
        {readinessError && (
          <Alert variant="error" title="Server Readiness Check Failed" style={{ marginBottom: "var(--space-4)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>{readinessError} — Unable to obtain authoritative launch status from backend.</span>
              <Button size="sm" variant="outline" onClick={fetchReadiness} disabled={readinessLoading}>
                Retry Readiness Check
              </Button>
            </div>
          </Alert>
        )}

        {ledgerError && (
          <Alert variant="error" title="Wallet Data Unavailable" style={{ marginBottom: "var(--space-4)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>{ledgerError} — Cannot verify wallet balance at this time.</span>
              <Button size="sm" variant="outline" onClick={fetchLedger} disabled={ledgerLoading}>
                Retry Wallet Check
              </Button>
            </div>
          </Alert>
        )}

        {domainsError && (
          <Alert variant="warning" title="Domain Verification Data Stale" style={{ marginBottom: "var(--space-4)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>{domainsError} — Domain records could not be refreshed.</span>
              <Button size="sm" variant="outline" onClick={fetchDomains} disabled={domainsLoading}>
                Retry Domain Check
              </Button>
            </div>
          </Alert>
        )}

        {activationSuccess ? (
          <Alert variant="success" title="Campaign Activated Successfully!" style={{ marginBottom: "var(--space-6)" }}>
            <p style={{ margin: "var(--space-2) 0" }}>
              Your campaign is now <strong>ACTIVE</strong> and eligible to enter real-time ad serving auctions.
            </p>
            <div style={{ display: "flex", gap: "var(--space-3)", marginTop: "var(--space-4)" }}>
              <Button variant="primary" onClick={() => navigate(`/advertiser/campaigns/${campaign.id}`)}>
                View Campaign Dashboard
              </Button>
              <Button variant="outline" onClick={() => navigate("/advertiser")}>
                Return to Advertiser Dashboard
              </Button>
            </div>
          </Alert>
        ) : (
          <>
            {activationError && (
              <Alert variant="error" title="Activation Blocked by Server" style={{ marginBottom: "var(--space-4)" }}>
                {activationError}
              </Alert>
            )}

            {/* Authoritative Blockers Notice */}
            {!readinessLoading && blockers.length > 0 && (
              <Alert variant="error" title={`Launch Blocked (${blockers.length} issue${blockers.length > 1 ? "s" : ""})`} style={{ marginBottom: "var(--space-4)" }}>
                <p style={{ margin: "0 0 var(--space-2) 0", fontSize: "var(--font-size-sm)" }}>
                  The following requirements must be resolved before this campaign can be activated:
                </p>
                <ul style={{ margin: 0, paddingLeft: "var(--space-4)", fontSize: "var(--font-size-sm)" }}>
                  {blockers.map((b, idx) => (
                    <li key={idx} style={{ marginBottom: "4px" }}>
                      <strong>[{b.entity_type.toUpperCase()}{b.entity_name ? `: ${b.entity_name}` : ""}]</strong> {b.message}
                    </li>
                  ))}
                </ul>
              </Alert>
            )}

            {/* Authoritative Warnings Notice */}
            {!readinessLoading && warnings.length > 0 && (
              <Alert variant="warning" title={`Launch Advisories (${warnings.length})`} style={{ marginBottom: "var(--space-4)" }}>
                <ul style={{ margin: 0, paddingLeft: "var(--space-4)", fontSize: "var(--font-size-sm)" }}>
                  {warnings.map((w, idx) => (
                    <li key={idx} style={{ marginBottom: "4px" }}>
                      <strong>[{w.entity_type.toUpperCase()}]</strong> {w.message}
                    </li>
                  ))}
                </ul>
              </Alert>
            )}

            {/* Pre-Flight Launch Checklist */}
            <div
              style={{
                background: "var(--color-bg-subtle)",
                borderRadius: "var(--radius-lg)",
                padding: "var(--space-4)",
                marginBottom: "var(--space-6)",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-3)" }}>
                <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: 0 }}>
                  Pre-Flight Launch Checklist
                </h3>
                <span style={{ fontSize: "var(--font-size-xs)", color: readinessLoading ? "var(--color-text-muted)" : isServerReady ? "var(--color-success)" : "var(--color-error)" }}>
                  {readinessLoading ? "Checking server readiness..." : isServerReady ? "✓ All Server Gates Passed" : `✗ ${blockers.length} Blocker(s) Remaining`}
                </span>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "var(--space-3)" }}>
                  {/* Budget Item */}
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                    <span style={{ color: hasBudget ? "var(--color-success)" : "var(--color-error)", fontWeight: "bold" }}>
                      {hasBudget ? "✓" : "✗"}
                    </span>
                    <span>Daily &amp; Lifetime Budget Configured</span>
                  </div>

                  {/* Ad Sets Item */}
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                    <span style={{ color: hasActiveAdSets ? "var(--color-success)" : "var(--color-error)", fontWeight: "bold" }}>
                      {hasActiveAdSets ? "✓" : "✗"}
                    </span>
                    <span>Active Ad Sets Defined ({activeAdSetsCount}/{totalAdSetsCount} active)</span>
                  </div>

                  {/* Creatives Item */}
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                    <span style={{ color: hasActiveCreatives ? "var(--color-success)" : "var(--color-error)", fontWeight: "bold" }}>
                      {hasActiveCreatives ? "✓" : "✗"}
                    </span>
                    <span>Creative Assets Configured ({activeCreativesCount}/{totalCreativesCount} active)</span>
                  </div>

                  {/* Creative Moderation */}
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                    <span style={{ color: unapprovedCount === 0 && totalCreativesCount > 0 ? "var(--color-success)" : "var(--color-error)", fontWeight: "bold" }}>
                      {unapprovedCount === 0 && totalCreativesCount > 0 ? "✓" : "✗"}
                    </span>
                    <span>
                      Creative Moderation ({approvedCreativesCount}/{totalCreativesCount} Approved)
                    </span>
                  </div>

                  {/* Landing Domain Verification */}
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                    <span style={{ color: unverifiedCount === 0 && totalCreativesCount > 0 ? "var(--color-success)" : "var(--color-error)", fontWeight: "bold" }}>
                      {unverifiedCount === 0 && totalCreativesCount > 0 ? "✓" : "✗"}
                    </span>
                    <span>
                      Landing Domain Verification ({verifiedCreativesCount}/{totalCreativesCount} Verified)
                    </span>
                  </div>

                  {/* Targeting */}
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                    <span style={{ color: hasTargeting ? "var(--color-success)" : "var(--color-error)", fontWeight: "bold" }}>
                      {hasTargeting ? "✓" : "✗"}
                    </span>
                    <span>Targeting Configured {hasTargeting ? "(Defined)" : "(Missing)"}</span>
                  </div>

                  {/* Wallet Balance */}
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                    <span style={{ color: hasPositiveBalance ? "var(--color-success)" : "var(--color-error)", fontWeight: "bold" }}>
                      {hasPositiveBalance ? "✓" : "✗"}
                    </span>
                    <span>
                      Wallet Balance Funded ({money(balance)})
                    </span>
                  </div>
                </div>

              {/* Wallet Top-up Drawer if balance insufficient */}
              {!hasPositiveBalance && (
                <div style={{ marginTop: "var(--space-4)", paddingTop: "var(--space-4)", borderTop: "1px solid var(--color-border)" }}>
                  <div style={{ color: "var(--color-error)", fontWeight: "var(--font-weight-semibold)", marginBottom: "var(--space-2)" }}>
                    ⚠️ Positive wallet balance required to activate ad serving.
                  </div>
                  <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
                    <Input
                      placeholder="Amount (₹)"
                      type="number"
                      min="100"
                      value={topUpAmount}
                      onChange={(e) => setTopUpAmount(e.target.value)}
                      style={{ maxWidth: "160px" }}
                      disabled={topUpLoading}
                    />
                    <Button variant="outline" size="sm" onClick={handleQuickTopUp} isLoading={topUpLoading}>
                      Top Up Wallet
                    </Button>
                  </div>
                  {topUpOrder?.qr_image_url && (
                    <div style={{ marginTop: "var(--space-3)" }}>
                      <p style={{ fontSize: "var(--font-size-xs)", margin: "0 0 var(--space-2) 0" }}>Scan UPI QR to deposit ₹{topUpAmount}:</p>
                      <img src={topUpOrder.qr_image_url} alt="Deposit QR" style={{ maxWidth: "180px", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)" }} />
                    </div>
                  )}
                  {topUpError && <p className="form-error" style={{ marginTop: "var(--space-2)", color: "var(--color-error)" }}>{topUpError}</p>}
                </div>
              )}
            </div>

            {/* Campaign Summary Card */}
            <div style={{ border: "1px solid var(--color-border)", borderRadius: "var(--radius-lg)", padding: "var(--space-4)", marginBottom: "var(--space-4)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-3)" }}>
                <div>
                  <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-bold)", margin: "0 0 var(--space-1) 0" }}>
                    {campaign.name}
                  </h3>
                  <div className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
                    Pricing: {BID_TYPES[campaign.bid_type] || "CPM"} @ ${campaign.bid_amount} | Daily Cap: {campaign.freq_cap_daily || 10} impr/user | Hourly: {campaign.freq_cap_hourly || 3}
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                  <span className="badge badge-subtle">Lifecycle: {readiness?.lifecycle_state || "Draft"}</span>
                  <StatusBadge status={campaign.status} />
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-4)", background: "var(--color-bg-subtle)", padding: "var(--space-3)", borderRadius: "var(--radius-md)" }}>
                <div>
                  <span className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>Daily Spend Ceiling</span>
                  <div style={{ fontWeight: "var(--font-weight-bold)", fontSize: "var(--font-size-base)" }}>${campaign.budget_daily}</div>
                </div>
                <div>
                  <span className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>Total Lifetime Ceiling</span>
                  <div style={{ fontWeight: "var(--font-weight-bold)", fontSize: "var(--font-size-base)" }}>${campaign.budget_total}</div>
                </div>
              </div>
            </div>

            {/* Ad Sets & Creatives Breakdown */}
            <div style={{ border: "1px solid var(--color-border)", borderRadius: "var(--radius-lg)", padding: "var(--space-4)", marginBottom: "var(--space-6)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-3)" }}>
                <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-bold)", margin: 0 }}>
                  Ad Sets ({adSets.length}) &amp; Ads Breakdown
                </h3>
                <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>
                  Hierarchy: Campaign Master Budget → Ad Set Sub-budgets → Ads
                </span>
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
                {adSets.map((adSet) => {
                  const setCreatives = creatives.filter((c) => c.ad_set_id === adSet.id);
                  const isAdSetActive = adSet.status === 1;

                  return (
                    <div key={adSet.id} style={{ border: "1px solid var(--color-border-subtle)", borderRadius: "var(--radius-md)", padding: "var(--space-3)" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-2)" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                          <strong>{adSet.name}</strong>
                          <StatusBadge status={adSet.status} size="sm" />
                          <span className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
                            Sub-budget: ${adSet.daily_budget != null ? adSet.daily_budget : campaign.budget_daily} | Bid: ${adSet.bid_amount}
                          </span>
                        </div>
                        <span className="badge badge-subtle">{setCreatives.length} Ads</span>
                      </div>

                      {setCreatives.length === 0 ? (
                        <div className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
                          ⚠️ No ads assigned to this Ad Set yet.
                        </div>
                      ) : (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
                          {setCreatives.map((c) => {
                            const isApproved = c.moderation_status === 2;
                            return (
                              <span 
                                key={c.id} 
                                className="chip chip-include"
                                style={{
                                  border: !isApproved ? "1px solid var(--color-warning)" : undefined,
                                }}
                              >
                                {c.title} ({c.width}×{c.height}) — {isApproved ? "Approved" : "Pending Moderation"}
                              </span>
                            );
                          })}
                        </div>
                      )}

                      {!isAdSetActive && (
                        <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-warning)", marginTop: "var(--space-2)" }}>
                          ⚠️ Note: Ads in this paused Ad Set will not be served during live auctions until the Ad Set is activated.
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Wizard Activation Submission Controls */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: "var(--space-4)", borderTop: "1px solid var(--color-border)" }}>
              <Button variant="outline" onClick={onBack} disabled={submitting}>
                ← Back to Ads
              </Button>

              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
                <span className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
                  Lifecycle: <strong>{readiness?.lifecycle_state || (campaign.status === 5 ? "Draft" : campaign.status === 1 ? "Active" : "Paused")}</strong> | Current Status: <strong>{campaign.status === 5 ? "DRAFT" : campaign.status === 1 ? "ACTIVE" : "PAUSED"}</strong>
                </span>

                <Button
                  variant="primary"
                  onClick={handleActivate}
                  disabled={!canAttemptActivation || submitting}
                  isLoading={submitting}
                  title={!isServerReady ? `Launch blocked: ${blockers[0]?.message || "Unmet launch requirements"}` : "Launch campaign into active serving"}
                >
                  🚀 Activate Campaign
                </Button>
              </div>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
