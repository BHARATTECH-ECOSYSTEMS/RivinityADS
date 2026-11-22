import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { 
  listSlots, 
  requestAds, 
  type SlotData,
  listPublisherProperties,
  type PublisherProperty 
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import type { AdResponse } from "../../types";
import {
  PageContainer,
  PageHeader,
  Card,
  Button,
  StatusBadge,
  Alert,
  EmptyState,
} from "../../components/ui";

export default function Integration() {
  const { session } = useAuth();
  const [slots, setSlots] = useState<SlotData[]>([]);
  const [properties, setProperties] = useState<PublisherProperty[]>([]);
  const [selectedSlot, setSelectedSlot] = useState<string>("");
  const [copiedTab, setCopiedTab] = useState<string | null>(null);
  const [ad, setAd] = useState<AdResponse | null>(null);
  const [requestId, setRequestId] = useState("");
  const [loadingAd, setLoadingAd] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);
  const [activeSnippetTab, setActiveSnippetTab] = useState<"iframe" | "sdk" | "s2s">("iframe");

  useEffect(() => {
    const fetchData = async () => {
      try {
        const publisherId = session?.user?.publisher_id;
        const [slotsData, propsData] = await Promise.all([
          listSlots(publisherId || undefined),
          publisherId ? listPublisherProperties(publisherId) : Promise.resolve([])
        ]);
        setSlots(slotsData);
        setProperties(propsData);
        if (slotsData.length > 0) setSelectedSlot(slotsData[0].slot_id);
      } catch {
        // Fallback if data inaccessible
      }
    };
    void fetchData();
  }, [session?.user?.publisher_id]);

  const currentSlot = slots.find((s) => s.slot_id === selectedSlot);
  const currentProperty = properties.find((p) => p.id === currentSlot?.property_id);
  const isPropertyVerified = currentProperty ? currentProperty.verification_status === "VERIFIED" : true;

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
        title="Publisher Integration &amp; Ad Delivery"
        description="Embed ad units into your web pages, mobile applications, or backend conversational AI pipelines."
        actions={
          <div style={{ display: "flex", gap: "8px" }}>
            <Link to="/publisher/properties" className="btn btn-secondary btn-sm">
              Properties &amp; Verification →
            </Link>
            <Link to="/publisher/ad-units" className="btn btn-secondary btn-sm">
              Ad Units →
            </Link>
          </div>
        }
      />

      {/* Unverified Property Prerequisite Notice */}
      {currentSlot && currentProperty && !isPropertyVerified && (
        <Alert variant="warning" title="Property Verification Required" style={{ marginBottom: "20px" }}>
          This placement slot is bound to property <strong>{currentProperty.name}</strong> (<code style={{ fontFamily: "var(--font-mono)" }}>{currentProperty.domain}</code>), which is currently unverified. The ad server will reject live traffic with HTTP 403 Forbidden until domain ownership is verified.{" "}
          <Link to="/publisher/properties" style={{ textDecoration: "underline", fontWeight: 600 }}>
            Complete Domain Verification in Properties →
          </Link>
        </Alert>
      )}

      {/* Slot Selector & Status Banner */}
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

          <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
            {currentSlot && (
              <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                <StatusBadge 
                  status={currentSlot.status === 1 ? "active" : "paused"} 
                  label={currentSlot.status === 1 ? "Active Slot" : "Paused"}
                  size="sm" 
                />
                {currentProperty ? (
                  <StatusBadge 
                    variant={isPropertyVerified ? "success" : "warning"} 
                    label={isPropertyVerified ? `Domain Verified (${currentProperty.domain})` : `Unverified Domain (${currentProperty.domain})`} 
                    size="sm" 
                  />
                ) : (
                  <StatusBadge variant="default" label="Global / Unassigned Domain" size="sm" />
                )}
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

      {/* Delivery Architecture Overview */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "16px", marginBottom: "24px" }}>
        <Card style={{ padding: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: 700, fontSize: "0.875rem" }}>
            <span style={{ fontSize: "1.125rem" }}>🖼️</span> Standard Web Iframe
          </div>
          <p style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)", margin: "8px 0 0" }}>
            Zero JavaScript dependencies. Isolated CSS &amp; DOM sandbox preventing host collision. Direct server-rendered creative.
          </p>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "6px" }}>
            Best for: Standard blog sidebars, editorial headers, and CMS widgets.
          </div>
        </Card>

        <Card style={{ padding: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: 700, fontSize: "0.875rem" }}>
            <span style={{ fontSize: "1.125rem" }}>⚡</span> Dynamic JavaScript SDK
          </div>
          <p style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)", margin: "8px 0 0" }}>
            Asynchronous tag with visitor consent handshake (CMP compliance), dynamic container sizing, and responsive lazy loading.
          </p>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "6px" }}>
            Best for: Single Page Applications (SPAs), Next.js / Vite apps, and dynamic feeds.
          </div>
        </Card>

        <Card style={{ padding: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: 700, fontSize: "0.875rem" }}>
            <span style={{ fontSize: "1.125rem" }}>🤖</span> Server-to-Server (S2S) HMAC
          </div>
          <p style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)", margin: "8px 0 0" }}>
            Cryptographically signed requests using HMAC-SHA256, API keys, and timestamps. Protects platform identity and visitor privacy.
          </p>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "6px" }}>
            Best for: Conversational AI chatbots, LLM tools, mobile backend relays.
          </div>
        </Card>
      </div>

      {/* Platform Invariants & Verification Callout */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "16px", marginBottom: "24px" }}>
        <div style={{ background: "var(--color-bg-subtle)", border: "1px solid var(--border-color)", borderRadius: "var(--radius-md)", padding: "14px 16px" }}>
          <div style={{ fontWeight: 600, fontSize: "0.8125rem", color: "var(--color-text-primary)", display: "flex", alignItems: "center", gap: "6px" }}>
            <span>🔒</span> Origin &amp; Referer Domain Matching
          </div>
          <p style={{ margin: "6px 0 0", fontSize: "0.75rem", color: "var(--color-text-secondary)", lineHeight: 1.5 }}>
            To prevent ad spoofing and traffic hijacking, the ad server validates the request <code>Origin</code> and <code>Referer</code> headers against your verified property domain. Requests from mismatched domains are rejected with HTTP 403 Forbidden.
          </p>
        </div>

        <div style={{ background: "var(--color-bg-subtle)", border: "1px solid var(--border-color)", borderRadius: "var(--radius-md)", padding: "14px 16px" }}>
          <div style={{ fontWeight: 600, fontSize: "0.8125rem", color: "var(--color-text-primary)", display: "flex", alignItems: "center", gap: "6px" }}>
            <span>🧪</span> Non-Billable Test Mode &amp; Sandbox Safety
          </div>
          <p style={{ margin: "6px 0 0", fontSize: "0.75rem", color: "var(--color-text-secondary)", lineHeight: 1.5 }}>
            Requests sent via the live test inspector above or within editor preview sandboxes are strictly non-billable. They generate zero balance deductions for advertisers and zero publisher earnings adjustments.
          </p>
        </div>
      </div>

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

      {/* Supported Formats & Conversational AI Surface Guide */}
      <Card
        title="Supported Formats &amp; Placement Specifications"
        subtitle="Match ad placement dimensions, creative formats, and opt into Conversational AI surfaces."
        style={{ marginBottom: "24px" }}
      >
        <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1.8fr", gap: "24px", alignItems: "start" }}>
          <div>
            <h4 style={{ fontSize: "0.875rem", fontWeight: 700, margin: "0 0 8px" }}>Supported Creative Formats</h4>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginBottom: "16px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8125rem", padding: "6px 0", borderBottom: "1px solid var(--border-color)" }}>
                <span><strong>Banner</strong> (Format 1)</span>
                <span style={{ color: "var(--color-text-secondary)" }}>Display Image / HTML creatives</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8125rem", padding: "6px 0", borderBottom: "1px solid var(--border-color)" }}>
                <span><strong>Native</strong> (Format 2)</span>
                <span style={{ color: "var(--color-text-secondary)" }}>Feed cards &amp; contextual in-stream units</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8125rem", padding: "6px 0", borderBottom: "1px solid var(--border-color)" }}>
                <span><strong>Video</strong> (Format 3)</span>
                <span style={{ color: "var(--color-text-secondary)" }}>Outstream &amp; VAST-compatible video</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8125rem", padding: "6px 0", borderBottom: "1px solid var(--border-color)" }}>
                <span><strong>Interstitial</strong> (Format 4)</span>
                <span style={{ color: "var(--color-text-secondary)" }}>Full-screen overlay transitions</span>
              </div>
            </div>

            <h4 style={{ fontSize: "0.875rem", fontWeight: 700, margin: "0 0 8px" }}>Standard IAB Display Dimensions</h4>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8125rem", padding: "6px 0", borderBottom: "1px solid var(--border-color)" }}>
                <span>Medium Rectangle</span>
                <code style={{ fontFamily: "var(--font-mono)" }}>300 × 250 px</code>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8125rem", padding: "6px 0", borderBottom: "1px solid var(--border-color)" }}>
                <span>Leaderboard</span>
                <code style={{ fontFamily: "var(--font-mono)" }}>728 × 90 px</code>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8125rem", padding: "6px 0", borderBottom: "1px solid var(--border-color)" }}>
                <span>Wide Skyscraper</span>
                <code style={{ fontFamily: "var(--font-mono)" }}>160 × 600 px</code>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8125rem", padding: "6px 0", borderBottom: "1px solid var(--border-color)" }}>
                <span>Mobile Leaderboard</span>
                <code style={{ fontFamily: "var(--font-mono)" }}>320 × 50 px</code>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8125rem", padding: "6px 0" }}>
                <span>Billboard</span>
                <code style={{ fontFamily: "var(--font-mono)" }}>970 × 250 px</code>
              </div>
            </div>
          </div>

          <div style={{ background: "var(--color-bg-subtle)", padding: "16px", borderRadius: "var(--radius-md)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: 700, fontSize: "0.875rem", color: "var(--color-primary)" }}>
              <span>💬</span> Conversational AI Surfaces (Chat AI)
            </div>
            <p style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)", margin: "8px 0 12px" }}>
              Placements flagged with <code>is_chat_surface: true</code> require explicit publisher opt-in and are optimized for conversational AI engines, chatbot dialogues, and AI assistant interfaces.
            </p>
            <ul style={{ margin: 0, paddingLeft: "18px", fontSize: "0.75rem", color: "var(--color-text-secondary)", lineHeight: 1.6 }}>
              <li><strong>Explicit Surface Opt-In:</strong> Check &quot;Conversational AI Surface&quot; when creating or editing a placement slot.</li>
              <li><strong>Context-Aware Matching:</strong> Real-time sector affinity matching against user conversation prompts.</li>
              <li><strong>Native Stream Formatting:</strong> Lightweight cards formatted specifically for LLM text outputs.</li>
              <li><strong>Zero PII / Privacy Hashing:</strong> User identifiers must be 64-character SHA-256 hashes prior to request submission.</li>
              <li><strong>Premium Yield:</strong> Eligible for conversational advertiser bidding and higher floor pricing.</li>
            </ul>
          </div>
        </div>
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
