import { useState, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  activateCampaign,
  getAdvertiserLedger,
  listAdvertiserDomains,
  createAdvertiserPaymentRequest,
  type LedgerResponse,
  type AdvertiserDomain,
  type PaymentOrderResponse,
} from "../../../api/client";
import { useAuth } from "../../../auth/AuthContext";
import { BID_TYPES, CREATIVE_TYPES, type Campaign, type AdSet, type Creative } from "../../../types";
import { money } from "../../../lib/format";
import { Button, Alert, Card, StatusBadge, Input } from "../../../components/ui";

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

  const [ledger, setLedger] = useState<LedgerResponse | null>(null);
  const [domains, setDomains] = useState<AdvertiserDomain[]>([]);
  const [loading, setLoading] = useState(false);
  const [activationError, setActivationError] = useState<string | null>(null);
  const [activationSuccess, setActivationSuccess] = useState(false);

  // Quick top-up state
  const [topUpAmount, setTopUpAmount] = useState<string>("500");
  const [topUpLoading, setTopUpLoading] = useState(false);
  const [topUpOrder, setTopUpOrder] = useState<PaymentOrderResponse | null>(null);
  const [topUpError, setTopUpError] = useState<string | null>(null);

  useEffect(() => {
    if (session?.user.advertiser_id) {
      void getAdvertiserLedger(session.user.advertiser_id).then(setLedger).catch(() => {});
      void listAdvertiserDomains().then(setDomains).catch(() => {});
    }
  }, [session?.user.advertiser_id]);

  const balance = ledger?.balance ?? 0;

  // Evaluate launch prerequisites
  const hasBudget = campaign.budget_daily > 0 && campaign.budget_total >= campaign.budget_daily;
  const hasAdSets = adSets.length > 0;
  const hasCreatives = creatives.length > 0;
  const hasActiveCreatives = creatives.some((c) => c.status === 1);
  const hasPositiveBalance = balance > 0;

  // Check creative moderation and domain verification
  const unapprovedCreatives = creatives.filter((c) => c.moderation_status !== 2); // not APPROVED
  const unverifiedCreatives = creatives.filter((c) => {
    try {
      const host = new URL(c.landing_url).hostname.toLowerCase();
      return !domains.some(
        (d) => d.status === "VERIFIED" && (d.domain.toLowerCase() === host || host.endsWith("." + d.domain.toLowerCase()))
      );
    } catch {
      return true;
    }
  });

  const canAttemptActivation =
    hasBudget &&
    hasAdSets &&
    hasCreatives &&
    hasActiveCreatives &&
    hasPositiveBalance &&
    campaign.status !== 1;

  const handleActivate = async () => {
    if (loading || !canAttemptActivation) return;
    setLoading(true);
    setActivationError(null);

    try {
      const result = await activateCampaign(campaign.id);
      if (result.status === 1) {
        setActivationSuccess(true);
        onActivated(result);
      } else {
        setActivationError("Server responded, but campaign status was not set to active.");
      }
    } catch (err: unknown) {
      setActivationError(err instanceof Error ? err.message : "Failed to activate campaign");
    } finally {
      setLoading(false);
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
        <div style={{ marginBottom: "var(--space-4)" }}>
          <h2 style={{ fontSize: "var(--font-size-xl)", fontWeight: "var(--font-weight-bold)", margin: "0 0 var(--space-1) 0" }}>
            Step 4: Campaign Review & Activation
          </h2>
          <p className="text-muted" style={{ margin: 0 }}>
            Review campaign hierarchy, verify launch readiness, and submit for server activation.
          </p>
        </div>

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
              <Alert variant="error" title="Activation Blocked" style={{ marginBottom: "var(--space-4)" }}>
                {activationError}
              </Alert>
            )}

            {/* Launch Readiness Checklist */}
            <div
              style={{
                background: "var(--color-bg-subtle)",
                borderRadius: "var(--radius-lg)",
                padding: "var(--space-4)",
                marginBottom: "var(--space-6)",
              }}
            >
              <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-3) 0" }}>
                Pre-Flight Launch Checklist
              </h3>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "var(--space-3)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                  <span style={{ color: hasBudget ? "var(--color-success)" : "var(--color-error)", fontWeight: "bold" }}>
                    {hasBudget ? "✓" : "✗"}
                  </span>
                  <span>Daily & Lifetime Budget Configured</span>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                  <span style={{ color: hasAdSets ? "var(--color-success)" : "var(--color-error)", fontWeight: "bold" }}>
                    {hasAdSets ? "✓" : "✗"}
                  </span>
                  <span>At least one Ad Set defined ({adSets.length} configured)</span>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                  <span style={{ color: hasCreatives ? "var(--color-success)" : "var(--color-error)", fontWeight: "bold" }}>
                    {hasCreatives ? "✓" : "✗"}
                  </span>
                  <span>Creative assets configured ({creatives.length} total)</span>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                  <span style={{ color: unapprovedCreatives.length === 0 ? "var(--color-success)" : "var(--color-warning)", fontWeight: "bold" }}>
                    {unapprovedCreatives.length === 0 ? "✓" : "⚠"}
                  </span>
                  <span>
                    Creative Moderation ({creatives.length - unapprovedCreatives.length}/{creatives.length} Approved)
                  </span>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                  <span style={{ color: unverifiedCreatives.length === 0 ? "var(--color-success)" : "var(--color-warning)", fontWeight: "bold" }}>
                    {unverifiedCreatives.length === 0 ? "✓" : "⚠"}
                  </span>
                  <span>
                    Landing Domain Verification ({creatives.length - unverifiedCreatives.length}/{creatives.length} Verified)
                  </span>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                  <span style={{ color: hasPositiveBalance ? "var(--color-success)" : "var(--color-error)", fontWeight: "bold" }}>
                    {hasPositiveBalance ? "✓" : "✗"}
                  </span>
                  <span>
                    Wallet Balance Funded ({money(balance)})
                  </span>
                </div>
              </div>

              {!hasPositiveBalance && (
                <div style={{ marginTop: "var(--space-4)", paddingTop: "var(--space-4)", borderTop: "1px solid var(--color-border)" }}>
                  <div style={{ color: "var(--color-error-text)", fontWeight: "var(--font-weight-semibold)", marginBottom: "var(--space-2)" }}>
                    ⚠ Positive wallet balance required to activate ad serving.
                  </div>
                  <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
                    <Input
                      placeholder="Amount (₹)"
                      type="number"
                      min="100"
                      value={topUpAmount}
                      onChange={(e) => setTopUpAmount(e.target.value)}
                      style={{ maxWidth: "160px" }}
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
                  {topUpError && <p className="form-error" style={{ marginTop: "var(--space-2)" }}>{topUpError}</p>}
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
                    Pricing: {BID_TYPES[campaign.bid_type] || "CPM"} @ ${campaign.bid_amount} | Daily Cap: {campaign.freq_cap_daily || 10} impr/user
                  </div>
                </div>
                <StatusBadge status={campaign.status} />
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
              <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-bold)", margin: "0 0 var(--space-3) 0" }}>
                Ad Sets ({adSets.length}) & Ads Breakdown
              </h3>

              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
                {adSets.map((adSet) => {
                  const setCreatives = creatives.filter((c) => c.ad_set_id === adSet.id);
                  return (
                    <div key={adSet.id} style={{ border: "1px solid var(--color-border-subtle)", borderRadius: "var(--radius-md)", padding: "var(--space-3)" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-2)" }}>
                        <div>
                          <strong>{adSet.name}</strong>
                          <span className="text-muted" style={{ fontSize: "var(--font-size-xs)", marginLeft: "var(--space-2)" }}>
                            Sub-budget: ${adSet.daily_budget != null ? adSet.daily_budget : campaign.budget_daily} | Bid: ${adSet.bid_amount}
                          </span>
                        </div>
                        <span className="badge badge-subtle">{setCreatives.length} Ads</span>
                      </div>

                      {setCreatives.length === 0 ? (
                        <div className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
                          ⚠ No ads assigned to this Ad Set yet.
                        </div>
                      ) : (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
                          {setCreatives.map((c) => (
                            <span key={c.id} className="chip chip-include">
                              {c.title} ({c.width}x{c.height}) - {c.moderation_status === 2 ? "Approved" : "Pending"}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Wizard Activation Submission Controls */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: "var(--space-4)", borderTop: "1px solid var(--color-border)" }}>
              <Button variant="outline" onClick={onBack}>
                ← Back to Ads
              </Button>

              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
                <span className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
                  Current Status: <strong>{campaign.status === 5 ? "DRAFT" : campaign.status === 1 ? "ACTIVE" : "PAUSED"}</strong>
                </span>

                <Button
                  variant="primary"
                  onClick={handleActivate}
                  disabled={!canAttemptActivation || loading}
                  isLoading={loading}
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
