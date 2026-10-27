import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import ChatWindow from "../../components/Chat/ChatWindow";
import { ChatMessageData } from "../../components/Chat/ChatBubble";
import {
  createChatSession,
  sendChatMessage,
  ApiError,
  getAdvertiserLedger,
  activateCampaign,
  getCampaign,
  listAdSets,
  listCreatives,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { PageContainer, PageHeader, Card, Button, StatusBadge, Alert } from "../../components/ui";
import type { Campaign, AdSet, Creative } from "../../types";

export default function CampaignBuilder() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [campaignId, setCampaignId] = useState<number | null>(null);
  const [adSetId, setAdSetId] = useState<number | null>(null);
  const [currentStep, setCurrentStep] = useState<string>("campaign");

  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [adSets, setAdSets] = useState<AdSet[]>([]);
  const [creatives, setCreatives] = useState<Creative[]>([]);

  const [budgetConfigured, setBudgetConfigured] = useState(false);
  const [geoConfigured, setGeoConfigured] = useState(false);
  const [sectorConfigured, setSectorConfigured] = useState(false);
  const [paymentStatus, setPaymentStatus] = useState<"idle" | "opening" | "success" | "failed">("idle");
  const [messages, setMessages] = useState<ChatMessageData[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const { session } = useAuth();
  const navigate = useNavigate();

  const [walletBalance, setWalletBalance] = useState<number | null>(null);
  const [walletConfirmed, setWalletConfirmed] = useState(false);
  const [pollingTimeout, setPollingTimeout] = useState(false);
  const [activationLoading, setActivationLoading] = useState(false);
  const [campaignStatus, setCampaignStatus] = useState<number | null>(null);
  const [currentOrderId, setCurrentOrderId] = useState<string | null>(null);
  const [qrImageUrl, setQrImageUrl] = useState<string | null>(null);

  // Poll ledger for payment verification
  useEffect(() => {
    if (paymentStatus !== "success" || !session?.user.advertiser_id || !currentOrderId || walletConfirmed || pollingTimeout)
      return;

    let attempts = 0;
    const maxAttempts = qrImageUrl ? 100 : 20;
    let timer: ReturnType<typeof setInterval>;

    const checkLedger = async () => {
      try {
        const ledger = await getAdvertiserLedger(session.user.advertiser_id!);
        const hasMatchingTransaction = ledger.transactions.some((tx) => tx.reference_id === currentOrderId);

        if (hasMatchingTransaction) {
          setWalletBalance(ledger.balance);
          setWalletConfirmed(true);
          clearInterval(timer);
        } else {
          attempts++;
          if (attempts >= maxAttempts) {
            setPollingTimeout(true);
            clearInterval(timer);
          }
        }
      } catch {
        attempts++;
        if (attempts >= maxAttempts) {
          setPollingTimeout(true);
          clearInterval(timer);
        }
      }
    };

    checkLedger();
    timer = setInterval(checkLedger, 3000);

    return () => clearInterval(timer);
  }, [paymentStatus, session, currentOrderId, walletConfirmed, pollingTimeout, qrImageUrl]);

  // Load server-persisted hierarchy details whenever campaignId changes
  useEffect(() => {
    if (!campaignId) return;
    let isMounted = true;
    Promise.all([
      getCampaign(campaignId),
      listAdSets(campaignId).catch(() => []),
      listCreatives(campaignId).catch(() => []),
    ])
      .then(([campRes, adSetsRes, creatRes]) => {
        if (!isMounted) return;
        setCampaign(campRes);
        setAdSets(adSetsRes);
        setCreatives(creatRes);
        setCampaignStatus(campRes.status);
      })
      .catch(() => {});

    return () => {
      isMounted = false;
    };
  }, [campaignId, messages]);

  const handleActivate = async () => {
    if (!campaignId) return;
    setActivationLoading(true);
    setError(null);
    try {
      const res = await activateCampaign(campaignId);
      setCampaignStatus(res.status);
      setCampaign(res);
    } catch (err: any) {
      setError(err instanceof ApiError ? err.message : "Failed to activate campaign.");
    } finally {
      setActivationLoading(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    async function initSession() {
      try {
        const res = await createChatSession();
        if (mounted) {
          setSessionId(res.session_id);
          setMessages([
            {
              id: "initial",
              role: "assistant",
              content:
                "Hi! I can help you create and configure your advertising campaign using our 3-step hierarchy: Campaign → Ad Sets → Ads. Tell me what kind of campaign you want to run.",
            },
          ]);
        }
      } catch {
        if (mounted) {
          setPageError("Unable to start the campaign assistant. Please try again.");
        }
      }
    }
    initSession();
    return () => {
      mounted = false;
    };
  }, []);

  const handleSendMessage = async (message: string) => {
    if (!sessionId) return;

    const userMsg: ChatMessageData = {
      id: Date.now().toString(),
      role: "user",
      content: message,
    };

    setMessages((prev) => [...prev, userMsg]);
    setIsLoading(true);
    setError(null);
    try {
      const res = await sendChatMessage(sessionId, message);
      const astMsg: ChatMessageData = {
        id: (Date.now() + 1).toString(),
        role: "assistant",
        content: res.assistant_message,
        intent: res.intent,
        tool_status: res.tool_status,
      };
      setMessages((prev) => [...prev, astMsg]);

      // Track active hierarchy IDs from chat response
      if (res.current_campaign_id) {
        setCampaignId(res.current_campaign_id);
      }
      if (res.current_ad_set_id) {
        setAdSetId(res.current_ad_set_id);
      }
      if (res.current_step) {
        setCurrentStep(res.current_step);
      }

      const isToolSuccess = (status?: string | null) => !!status && status.startsWith("Success:");

      if (res.intent === "REQUEST_PAYMENT" && isToolSuccess(res.tool_status) && res.payload) {
        const payload = res.payload as any;
        const { order_id, amount, currency, qr_image_url } = payload;
        if (
          typeof order_id === "string" &&
          order_id.trim() !== "" &&
          amount !== undefined &&
          typeof currency === "string" &&
          currency.trim() !== ""
        ) {
          setCurrentOrderId(order_id);
          if (qr_image_url) {
            setQrImageUrl(qr_image_url);
            setPaymentStatus("success");
          } else {
            const keyId = import.meta.env.VITE_RAZORPAY_KEY_ID as string | undefined;
            if (!keyId) {
              setError("Payment configuration error: Missing public key.");
            } else if (!window.Razorpay) {
              setError("Payment checkout is currently unavailable.");
            } else {
              setPaymentStatus("opening");
              const options = {
                key: keyId,
                amount: amount as string | number,
                currency: currency,
                order_id: order_id,
                name: "RivinityADS",
                description: "Campaign Budget Deposit",
                handler: () => {
                  setPaymentStatus("success");
                },
                modal: {
                  ondismiss: () => {
                    setPaymentStatus("failed");
                  },
                },
              };
              const rzp = new window.Razorpay(options);
              if (rzp.on) {
                rzp.on("payment.failed", () => {
                  setPaymentStatus("failed");
                });
              }
              rzp.open();
            }
          }
        } else {
          setError("Payment configuration error: Invalid payload from server.");
        }
      } else if (res.intent === "CREATE_CAMPAIGN" && res.payload && res.payload.campaign_id != null) {
        const cid = Number(res.payload.campaign_id);
        if (cid) {
          setCampaignId(cid);
        }
      } else if (campaignId) {
        if (res.intent === "SET_BUDGET" && isToolSuccess(res.tool_status)) {
          setBudgetConfigured(true);
        } else if (res.intent === "SET_GEO" && isToolSuccess(res.tool_status)) {
          setGeoConfigured(true);
        } else if (res.intent === "SET_SECTOR" && isToolSuccess(res.tool_status)) {
          setSectorConfigured(true);
        }
      }
    } catch (err: any) {
      setError(err instanceof ApiError ? err.message : "Failed to send message. Please try again.");
    } finally {
      setIsLoading(false);
    }
  };

  if (pageError) {
    return (
      <PageContainer>
        <PageHeader title="Campaign Builder (AI Assistant)" />
        <Alert variant="error">{pageError}</Alert>
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      <PageHeader
        title="AI Campaign Assistant"
        description="Interact naturally to define campaigns, allocate Ad Set sub-budgets, configure target audiences, and launch ads."
        actions={
          campaignId ? (
            <div style={{ display: "flex", gap: "var(--space-2)" }}>
              <Button
                variant="outline"
                size="sm"
                onClick={() => navigate(`/advertiser/campaigns/new?campaign_id=${campaignId}&step=2`)}
              >
                Switch to Manual Wizard ↗
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={() => navigate(`/advertiser/campaigns/${campaignId}`)}
              >
                View Campaign Details →
              </Button>
            </div>
          ) : undefined
        }
      />

      {error && (
        <Alert variant="error" style={{ marginBottom: "var(--space-4)" }}>
          {error}
        </Alert>
      )}

      {/* Persisted Hierarchy Progress Bar */}
      {campaignId && (
        <Card style={{ marginBottom: "var(--space-4)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-3)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
              <strong style={{ fontSize: "var(--font-size-sm)" }}>
                Active Campaign: {campaign?.name || `#${campaignId}`}
              </strong>
              {campaignStatus !== null && <StatusBadge status={campaignStatus} />}
            </div>
            <div className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
              {adSets.length} Ad Sets | {creatives.length} Ads
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "var(--space-2)", fontSize: "var(--font-size-xs)" }}>
            <div style={{ padding: "var(--space-2)", borderRadius: "var(--radius-md)", background: "var(--color-bg-subtle)" }}>
              <span style={{ color: "var(--color-success)", fontWeight: "bold" }}>✓ Step 1: Campaign</span>
              <div className="text-muted" style={{ fontSize: "11px" }}>${campaign?.budget_daily || 0}/day ceiling</div>
            </div>

            <div style={{ padding: "var(--space-2)", borderRadius: "var(--radius-md)", background: "var(--color-bg-subtle)" }}>
              <span style={{ color: adSets.length > 0 ? "var(--color-success)" : "var(--color-text-muted)", fontWeight: "bold" }}>
                {adSets.length > 0 ? "✓" : "○"} Step 2: Ad Sets ({adSets.length})
              </span>
              <div className="text-muted" style={{ fontSize: "11px" }}>
                {adSetId ? `Selected: #${adSetId}` : "Targeting configured"}
              </div>
            </div>

            <div style={{ padding: "var(--space-2)", borderRadius: "var(--radius-md)", background: "var(--color-bg-subtle)" }}>
              <span style={{ color: creatives.length > 0 ? "var(--color-success)" : "var(--color-text-muted)", fontWeight: "bold" }}>
                {creatives.length > 0 ? "✓" : "○"} Step 3: Creatives ({creatives.length})
              </span>
              <div className="text-muted" style={{ fontSize: "11px" }}>
                {creatives.filter((c) => c.moderation_status === 2).length} Approved
              </div>
            </div>

            <div style={{ padding: "var(--space-2)", borderRadius: "var(--radius-md)", background: "var(--color-bg-subtle)" }}>
              <span style={{ color: campaignStatus === 1 ? "var(--color-success)" : "var(--color-text-muted)", fontWeight: "bold" }}>
                {campaignStatus === 1 ? "✓ Live & Serving" : "○ Step 4: Activation"}
              </span>
              <div className="text-muted" style={{ fontSize: "11px" }}>
                {campaignStatus === 1 ? "Active" : "Draft Status"}
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* Payment States Notifications */}
      {paymentStatus === "success" && !walletConfirmed && !pollingTimeout && (
        <Alert variant="info" title="Payment Submitted" style={{ marginBottom: "var(--space-4)" }}>
          {qrImageUrl ? (
            <div style={{ textAlign: "center", margin: "var(--space-3) 0" }}>
              <p style={{ margin: "0 0 var(--space-2) 0" }}>Scan with any UPI app to deposit funds:</p>
              <img src={qrImageUrl} alt="Payment QR" style={{ maxWidth: "200px", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)" }} />
              <p className="text-muted" style={{ fontSize: "11px", marginTop: "var(--space-1)" }}>
                Waiting for bank ledger confirmation...
              </p>
            </div>
          ) : (
            <p style={{ margin: 0 }}>Please wait while your transaction is confirmed on the ledger.</p>
          )}
        </Alert>
      )}

      {walletConfirmed && campaignStatus !== 1 && (
        <Alert variant="success" title="Wallet Credited" style={{ marginBottom: "var(--space-4)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>Available balance: ₹{walletBalance?.toFixed(2) || "0.00"}. Ready to activate campaign.</span>
            <Button variant="primary" size="sm" onClick={handleActivate} isLoading={activationLoading}>
              🚀 Activate Campaign
            </Button>
          </div>
        </Alert>
      )}

      {campaignStatus === 1 && (
        <Alert variant="success" title="Campaign Activated!" style={{ marginBottom: "var(--space-4)" }}>
          This campaign is now live and serving ads across publisher inventory.
        </Alert>
      )}

      {/* Conversational Chat Interface */}
      <Card>
        <ChatWindow
          messages={messages}
          onSendMessage={handleSendMessage}
          isLoading={isLoading}
          error={error}
        />
      </Card>
    </PageContainer>
  );
}
