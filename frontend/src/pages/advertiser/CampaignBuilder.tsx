import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import ChatWindow from "../../components/Chat/ChatWindow";
import { ChatMessageData } from "../../components/Chat/ChatBubble";
import { createChatSession, sendChatMessage, ApiError, getAdvertiserLedger, activateCampaign } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";

export default function CampaignBuilder() {
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [campaignId, setCampaignId] = useState<string | null>(null);
    const [budgetConfigured, setBudgetConfigured] = useState(false);
    const [geoConfigured, setGeoConfigured] = useState(false);
    const [sectorConfigured, setSectorConfigured] = useState(false);
    const [paymentStatus, setPaymentStatus] = useState<"idle" | "opening" | "success" | "failed">("idle");
    const [messages, setMessages] = useState<ChatMessageData[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [pageError, setPageError] = useState<string | null>(null);
    const { session } = useAuth();
    const [walletBalance, setWalletBalance] = useState<number | null>(null);
    const [walletConfirmed, setWalletConfirmed] = useState(false);
    const [pollingTimeout, setPollingTimeout] = useState(false);
    const [activationLoading, setActivationLoading] = useState(false);
    const [campaignStatus, setCampaignStatus] = useState<number | null>(null);
    const [currentOrderId, setCurrentOrderId] = useState<string | null>(null);
    const [qrImageUrl, setQrImageUrl] = useState<string | null>(null);

    useEffect(() => {
        if (paymentStatus !== "success" || !session?.user.advertiser_id || !currentOrderId || walletConfirmed || pollingTimeout) return;
        
        let attempts = 0;
        const maxAttempts = qrImageUrl ? 100 : 20; // 5 mins for QR, 60s for modal
        let timer: ReturnType<typeof setInterval>;

        const checkLedger = async () => {
            try {
                const ledger = await getAdvertiserLedger(session.user.advertiser_id!);
                const hasMatchingTransaction = ledger.transactions.some(tx => tx.reference_id === currentOrderId);

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
            } catch (e) {
                attempts++;
                if (attempts >= maxAttempts) {
                    setPollingTimeout(true);
                    clearInterval(timer);
                }
            }
        };
        
        // Initial check
        checkLedger();
        timer = setInterval(checkLedger, 3000);

        return () => clearInterval(timer);
    }, [paymentStatus, session, currentOrderId, walletConfirmed, pollingTimeout]);

    const handleActivate = async () => {
        if (!campaignId) return;
        setActivationLoading(true);
        setError(null);
        try {
            const res = await activateCampaign(Number(campaignId));
            setCampaignStatus(res.status);
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
                    setMessages([{
                        id: "initial",
                        role: "assistant",
                        content: "Hi! I can help you create and configure your advertising campaign. Tell me what kind of campaign you want to create."
                    }]);
                }
            } catch (err: any) {
                if (mounted) {
                    setPageError("Unable to start the campaign assistant. Please try again.");
                }
            }
        }
        initSession();
        return () => { mounted = false; };
    }, []);

    const handleSendMessage = async (message: string) => {
        if (!sessionId) return;
        
        const userMsg: ChatMessageData = {
            id: Date.now().toString(),
            role: "user",
            content: message
        };
        
        setMessages(prev => [...prev, userMsg]);
        setIsLoading(true);
        setError(null);
        try {
            const res = await sendChatMessage(sessionId, message);
            const astMsg: ChatMessageData = {
                id: (Date.now() + 1).toString(),
                role: "assistant",
                content: res.assistant_message,
                intent: res.intent,
                tool_status: res.tool_status
            };
            setMessages(prev => [...prev, astMsg]);

            const isToolSuccess = (status?: string | null) => !!status && status.startsWith("Success:");

            if (res.intent === "REQUEST_PAYMENT" && isToolSuccess(res.tool_status) && res.payload) {
                const payload = res.payload as any;
                const { order_id, amount, currency, qr_image_url } = payload;
                if (typeof order_id === "string" && order_id.trim() !== "" && amount !== undefined && typeof currency === "string" && currency.trim() !== "") {
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
                                name: "OpenAdServer",
                                description: "Campaign Budget Deposit",
                                handler: () => {
                                    setPaymentStatus("success");
                                },
                                modal: {
                                    ondismiss: () => {
                                        setPaymentStatus("failed");
                                    }
                                }
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
            } else if (res.intent === "CREATE_CAMPAIGN" && res.payload && typeof res.payload.campaign_id === "string" && res.payload.campaign_id.trim() !== "") {
                setCampaignId(res.payload.campaign_id.trim());
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
            <div>
                <h1>Campaign Builder</h1>
                <div className="card">
                    <p className="error">{pageError}</p>
                </div>
            </div>
        );
    }

    return (
        <div className="campaign-builder">
            <h1>Campaign Builder</h1>
            <p className="muted" style={{marginBottom: "16px"}}>Create your campaign interactively using AI.</p>
            
            {campaignId && (
                <div className="card" style={{ marginBottom: "16px", display: "flex", justifyContent: "space-between", alignItems: "flex-start", padding: "12px 16px" }}>
                    <div>
                        <strong style={{ display: "block", marginBottom: "8px" }}>Campaign Configuration</strong>
                        <ul style={{ listStyle: "none", margin: 0, padding: 0, fontSize: "0.9em", lineHeight: "1.5" }}>
                            <li style={{ color: "var(--color-success, #10b981)" }}>✓ Draft campaign created ({campaignId})</li>
                            {budgetConfigured && <li style={{ color: "var(--color-success, #10b981)" }}>✓ Budget configured</li>}
                            {geoConfigured && <li style={{ color: "var(--color-success, #10b981)" }}>✓ Geographic targeting configured</li>}
                            {sectorConfigured && <li style={{ color: "var(--color-success, #10b981)" }}>✓ Sector targeting configured</li>}
                        </ul>
                    </div>
                    <Link to={`/advertiser/campaigns/${campaignId}`} className="btn">View Campaign</Link>
                </div>
            )}

            {paymentStatus === "success" && !walletConfirmed && !pollingTimeout && (
                <div className="card" style={{ marginBottom: "16px", padding: "12px 16px", borderColor: "var(--color-primary, #3b82f6)", backgroundColor: "rgba(59, 130, 246, 0.05)" }}>
                    <strong style={{ color: "var(--color-primary, #3b82f6)" }}>
                        {qrImageUrl ? "Waiting for payment..." : "Payment submitted. Waiting for wallet confirmation..."}
                    </strong>
                    {qrImageUrl && (
                        <div style={{ margin: "16px 0" }}>
                            <p>Scan this QR code with any UPI app to complete your payment.</p>
                            <img src={qrImageUrl} alt="Payment QR Code" style={{ maxWidth: "250px", border: "1px solid #e5e7eb", borderRadius: "8px", marginTop: "8px" }} />
                        </div>
                    )}
                    <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "0.9em" }}>
                        {qrImageUrl ? "Your transaction will be automatically verified after scanning." : "Please wait while we verify your transaction."}
                    </p>
                </div>
            )}

            {paymentStatus === "success" && pollingTimeout && !walletConfirmed && (
                <div className="card" style={{ marginBottom: "16px", padding: "12px 16px", borderColor: "var(--color-warning, #f59e0b)", backgroundColor: "rgba(245, 158, 11, 0.05)" }}>
                    <strong style={{ color: "var(--color-warning, #f59e0b)" }}>Verification is taking longer than expected</strong>
                    <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "0.9em" }}>Payment was submitted, but wallet confirmation is still pending. Please refresh and try again later.</p>
                </div>
            )}

            {walletConfirmed && campaignStatus !== 1 && (
                <div className="card" style={{ marginBottom: "16px", padding: "12px 16px", borderColor: "var(--color-success, #10b981)", backgroundColor: "rgba(16, 185, 129, 0.05)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div>
                        <strong style={{ color: "var(--color-success, #10b981)" }}>Payment confirmed. Your wallet has been credited.</strong>
                        <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "0.9em" }}>Current balance: ₹{walletBalance?.toFixed(2) || "0.00"}</p>
                    </div>
                    <button onClick={handleActivate} disabled={activationLoading} className="btn btn-primary">
                        {activationLoading ? "Activating..." : "Activate Campaign"}
                    </button>
                </div>
            )}

            {campaignStatus === 1 && (
                <div className="card" style={{ marginBottom: "16px", padding: "12px 16px", borderColor: "var(--color-success, #10b981)", backgroundColor: "rgba(16, 185, 129, 0.05)" }}>
                    <strong style={{ color: "var(--color-success, #10b981)" }}>Campaign activated successfully!</strong>
                    <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "0.9em" }}>Your ads are now eligible to be served.</p>
                </div>
            )}
            
            {paymentStatus === "failed" && (
                <div className="card" style={{ marginBottom: "16px", padding: "12px 16px", borderColor: "var(--color-error, #ef4444)", backgroundColor: "rgba(239, 68, 68, 0.05)" }}>
                    <strong style={{ color: "var(--color-error, #ef4444)" }}>Payment was not completed.</strong>
                    <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "0.9em" }}>You can request another payment if needed.</p>
                </div>
            )}

            <ChatWindow 
                messages={messages} 
                onSendMessage={handleSendMessage} 
                isLoading={isLoading} 
                error={error} 
            />
        </div>
    );
}
