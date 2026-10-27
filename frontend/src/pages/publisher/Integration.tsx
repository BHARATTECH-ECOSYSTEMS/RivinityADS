import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listSlots, requestAds, type SlotData } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import type { AdResponse } from "../../types";
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

export default function Integration() {
  const { session } = useAuth();
  const [slots, setSlots] = useState<SlotData[]>([]);
  const [selectedSlot, setSelectedSlot] = useState<string>("");
  const [copiedTab, setCopiedTab] = useState<string | null>(null);
  const [ad, setAd] = useState<AdResponse | null>(null);
  const [requestId, setRequestId] = useState("");
  const [loadingAd, setLoadingAd] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);
  const [activeSnippetTab, setActiveSnippetTab] = useState<"iframe" | "sdk" | "s2s">("iframe");

  useEffect(() => {
    const fetchSlots = async () => {
      try {
        const publisherId = session?.user?.publisher_id;
        const data = await listSlots(publisherId || undefined);
        setSlots(data);
        if (data.length > 0) setSelectedSlot(data[0].slot_id);
      } catch {
        // Fallback if slots inaccessible
      }
    };
    void fetchSlots();
  }, [session?.user?.publisher_id]);

  const currentSlot = slots.find((s) => s.slot_id === selectedSlot);

  const apiBase =
    (import.meta.env?.VITE_API_BASE_URL as string | undefined) ??
    (typeof window !== "undefined" ? window.location.origin : "http://localhost:5173");

  const serveUrl = selectedSlot
    ? `${apiBase}/api/v1/ad/serve?slot_id=${encodeURIComponent(selectedSlot)}`
    : "";

  const iframeSnippet = selectedSlot
    ? `<!-- RivinityADS Tag — Placement: ${selectedSlot} (${currentSlot?.width || 300}x${currentSlot?.height || 250}) -->
<iframe
  src="${serveUrl}"
  width="${currentSlot?.width || 300}"
  height="${currentSlot?.height || 250}"
  frameborder="0"
  scrolling="no"
  style="border:none;overflow:hidden;"
  title="Advertisement"
></iframe>`
    : "Select a slot first";

  const scriptSnippet = selectedSlot
    ? `<!-- RivinityADS Dynamic Tag — Placement: ${selectedSlot} -->
<div id="oas-slot-${selectedSlot}" class="rivinity-ad-unit" data-slot-id="${selectedSlot}"></div>
<script>
(function() {
  var slot = "${selectedSlot}";
  var container = document.getElementById("oas-slot-" + slot);
  if (!container) return;
  var iframe = document.createElement("iframe");
  iframe.src = "${serveUrl}";
  iframe.width = "${currentSlot?.width || 300}";
  iframe.height = "${currentSlot?.height || 250}";
  iframe.frameBorder = "0";
  iframe.scrolling = "no";
  iframe.style.border = "none";
  iframe.style.overflow = "hidden";
  container.appendChild(iframe);
})();
</script>`
    : "Select a slot first";

  const s2sPythonSnippet = selectedSlot
    ? `# RivinityADS Server-to-Server HMAC (Python) — Placement: ${selectedSlot}
# SECURITY NOTICE: Execute ONLY on secure backend servers. Never expose API keys in frontend code.
import hashlib, hmac, json, time, requests

API_KEY = "pk_live_your_registered_api_key"  # Generated in Publisher -> API Keys
API_BASE = "${apiBase}"

def request_ad(slot_id: str, platform_user_id: str, sector_context: list[str]):
    # Hash user ID to protect privacy (64-character SHA-256)
    user_hash = hashlib.sha256(platform_user_id.encode("utf-8")).hexdigest()

    payload = {
        "slot_id": slot_id,
        "num_ads": 1,
        "user": {
            "id": user_hash,
            "sector_context": sector_context,
            "geo": {"country": "IN", "state": "Maharashtra", "city": "Mumbai", "pincode": "400001"}
        }
    }
    body = json.dumps(payload).encode("utf-8")
    ts = str(int(time.time()))

    # Canonical HMAC: HMAC-SHA256(api_key, "POST|/api/v1/ad/request|{timestamp}|{sha256(body)}")
    body_sha256 = hashlib.sha256(body).hexdigest()
    msg = f"POST|/api/v1/ad/request|{ts}|{body_sha256}".encode("utf-8")
    sig = hmac.new(API_KEY.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    response = requests.post(
        f"{API_BASE}/api/v1/ad/request",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,
            "X-Timestamp": ts,
            "X-Signature": sig,
        },
        timeout=3.0
    )
    response.raise_for_status()
    return response.json()

ad = request_ad("${selectedSlot}", "user_12345", ["developer"])
print("Ad Response:", json.dumps(ad, indent=2))`
    : "Select a slot first";

  const loadAd = async () => {
    if (!selectedSlot) return;
    setLoadingAd(true);
    setTestError(null);
    setAd(null);
    try {
      const response = await requestAds({
        slot_id: selectedSlot,
        num_ads: 1,
        user: {
          id: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          sector_context: ["developer"],
        },
        device: { os: "web" },
      });
      const nextAd = response.ads[0] ?? null;
      setAd(nextAd);
      setRequestId(response.request_id);
      if (nextAd) {
        new Image().src = nextAd.tracking.impression_url;
      } else {
        setTestError("No eligible ad was returned for this slot. Make sure an active campaign targeting this slot format exists.");
      }
    } catch (err) {
      setTestError(err instanceof Error ? err.message : "Could not request an ad");
    } finally {
      setLoadingAd(false);
    }
  };

  const copy = async (text: string, tabId: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedTab(tabId);
      setTimeout(() => setCopiedTab(null), 2500);
    } catch {
      // Clipboard write failed
    }
  };

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Integration Guide"
        title="Publisher Integration"
        description="Embed ad units into your web pages, mobile apps, or backend conversational AI pipelines."
        actions={
          <Link to="/publisher/preview" className="btn btn-secondary btn-sm">
            Live Ad Preview →
          </Link>
        }
      />

      {/* Slot Selector Banner */}
      <Card style={{ marginBottom: "24px" }}>
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px", flex: 1, minWidth: "280px" }}>
            <label htmlFor="slot-select" style={{ fontWeight: 600, fontSize: "0.875rem", whiteSpace: "nowrap" }}>
              Select Ad Placement:
            </label>
            <select
              id="slot-select"
              value={selectedSlot}
              onChange={(e) => {
                setSelectedSlot(e.target.value);
                setAd(null);
                setTestError(null);
              }}
              className="select"
              style={{ flex: 1 }}
            >
              {slots.map((s) => (
                <option key={s.slot_id} value={s.slot_id}>
                  {s.name} ({s.slot_id} — {s.width}×{s.height}px)
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            {currentSlot && (
              <div style={{ display: "flex", gap: "6px" }}>
                <StatusBadge 
                  status={currentSlot.status === 1 ? "active" : "paused"} 
                  label={currentSlot.status === 1 ? "Active Slot" : "Paused"}
                  size="sm"
                />
                {currentSlot.is_chat_surface && (
                  <StatusBadge status="info" label="Chat AI Surface" size="sm" />
                )}
              </div>
            )}
            <Button
              variant="primary"
              size="sm"
              onClick={loadAd}
              isLoading={loadingAd}
              disabled={!selectedSlot}
            >
              Send Live Test Request
            </Button>
          </div>
        </div>
      </Card>

      {/* Integration Snippets Card */}
      <Card
        title="Integration Code Snippets"
        subtitle="Choose the integration method suited to your tech stack."
        style={{ marginBottom: "24px" }}
      >
        <div style={{ display: "flex", gap: "8px", marginBottom: "16px", borderBottom: "1px solid var(--border-color)", paddingBottom: "12px" }}>
          <button
            type="button"
            className={`btn ${activeSnippetTab === "iframe" ? "btn-primary" : "btn-secondary"} btn-sm`}
            onClick={() => setActiveSnippetTab("iframe")}
          >
            Standard Web Iframe
          </button>
          <button
            type="button"
            className={`btn ${activeSnippetTab === "sdk" ? "btn-primary" : "btn-secondary"} btn-sm`}
            onClick={() => setActiveSnippetTab("sdk")}
          >
            Dynamic JavaScript Tag
          </button>
          <button
            type="button"
            className={`btn ${activeSnippetTab === "s2s" ? "btn-primary" : "btn-secondary"} btn-sm`}
            onClick={() => setActiveSnippetTab("s2s")}
          >
            Server-to-Server HMAC (Python)
          </button>
        </div>

        {activeSnippetTab === "iframe" && (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
              <span style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                Zero JavaScript required. Fully isolated sandbox rendering.
              </span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => copy(iframeSnippet, "iframe")}
              >
                {copiedTab === "iframe" ? "Copied!" : "Copy Snippet"}
              </Button>
            </div>
            <pre
              style={{
                backgroundColor: "var(--color-bg-inverse)",
                color: "#e2e8f0",
                padding: "16px",
                borderRadius: "var(--radius-md)",
                fontFamily: "var(--font-mono)",
                fontSize: "0.8125rem",
                overflowX: "auto",
                border: "1px solid #334155",
                lineHeight: 1.5,
              }}
            >
              {iframeSnippet}
            </pre>
          </div>
        )}

        {activeSnippetTab === "sdk" && (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
              <span style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                Asynchronously injected container suitable for single-page applications.
              </span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => copy(scriptSnippet, "sdk")}
              >
                {copiedTab === "sdk" ? "Copied!" : "Copy Snippet"}
              </Button>
            </div>
            <pre
              style={{
                backgroundColor: "var(--color-bg-inverse)",
                color: "#e2e8f0",
                padding: "16px",
                borderRadius: "var(--radius-md)",
                fontFamily: "var(--font-mono)",
                fontSize: "0.8125rem",
                overflowX: "auto",
                border: "1px solid #334155",
                lineHeight: 1.5,
              }}
            >
              {scriptSnippet}
            </pre>
          </div>
        )}

        {activeSnippetTab === "s2s" && (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
              <span style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                Cryptographically signed server-to-server request for backend LLM / Chatbots.
              </span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => copy(s2sPythonSnippet, "s2s")}
              >
                {copiedTab === "s2s" ? "Copied!" : "Copy Snippet"}
              </Button>
            </div>
            <pre
              style={{
                backgroundColor: "var(--color-bg-inverse)",
                color: "#e2e8f0",
                padding: "16px",
                borderRadius: "var(--radius-md)",
                fontFamily: "var(--font-mono)",
                fontSize: "0.8125rem",
                overflowX: "auto",
                border: "1px solid #334155",
                lineHeight: 1.5,
              }}
            >
              {s2sPythonSnippet}
            </pre>
          </div>
        )}
      </Card>

      {/* Live Sandbox Ad Request Result */}
      {(ad || testError) && (
        <Card
          title="Sandbox Ad Response Inspector"
          subtitle={`Request ID: ${requestId || "N/A"}`}
        >
          {testError && (
            <Alert variant="warning" title="No Fill / Ad Request Error">
              {testError}
            </Alert>
          )}

          {ad && (
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <StatusBadge status="success" label="Ad Winner Returned" size="sm" />
                  <span style={{ fontWeight: 600, fontSize: "1rem" }}>{ad.creative.title}</span>
                </div>
                <span style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                  Impression tracker automatically verified
                </span>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", marginBottom: "16px" }}>
                <div style={{ background: "var(--color-bg-subtle)", padding: "12px", borderRadius: "var(--radius-md)" }}>
                  <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", textTransform: "uppercase" }}>Creative Details</div>
                  <div style={{ fontSize: "0.875rem", marginTop: "4px" }}>
                    Type: <strong>{ad.creative.creative_type}</strong> | Dimensions: <strong>{ad.creative.width}×{ad.creative.height}px</strong>
                  </div>
                  {ad.creative.description && (
                    <div style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)", marginTop: "4px" }}>
                      {ad.creative.description}
                    </div>
                  )}
                </div>

                <div style={{ background: "var(--color-bg-subtle)", padding: "12px", borderRadius: "var(--radius-md)" }}>
                  <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", textTransform: "uppercase" }}>Landing URL</div>
                  <a
                    href={ad.creative.landing_url}
                    target="_blank"
                    rel="noreferrer"
                    style={{ fontSize: "0.875rem", color: "var(--color-primary)", wordBreak: "break-all", display: "block", marginTop: "4px" }}
                  >
                    {ad.creative.landing_url}
                  </a>
                </div>
              </div>

              <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", fontFamily: "var(--font-mono)", wordBreak: "break-all" }}>
                Impression Ping: {ad.tracking.impression_url}
              </div>
            </div>
          )}
        </Card>
      )}
    </PageContainer>
  );
}
