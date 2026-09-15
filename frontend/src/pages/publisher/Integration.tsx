import { useEffect, useState } from "react";
import { listSlots, requestAds } from "../../api/client";
import type { SlotData } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import type { AdResponse } from "../../types";

export default function Integration() {
  const { session } = useAuth();
  const [slots, setSlots] = useState<SlotData[]>([]);
  const [selectedSlot, setSelectedSlot] = useState<string>("");
  const [copied, setCopied] = useState(false);
  const [ad, setAd] = useState<AdResponse | null>(null);
  const [requestId, setRequestId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch publisher's ad slots
  useEffect(() => {
    const fetchSlots = async () => {
      try {
        const data = await listSlots();
        setSlots(data);
        if (data.length > 0) setSelectedSlot(data[0].slot_id);
      } catch {
        // Slots may not be accessible — that's okay
      }
    };
    void fetchSlots();
  }, []);

  const currentSlot = slots.find((s) => s.slot_id === selectedSlot);

  // Build the base URL for the serve endpoint
  const apiBase =
    (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
    window.location.origin;

  const serveUrl = selectedSlot
    ? `${apiBase}/api/v1/ad/serve?slot_id=${encodeURIComponent(selectedSlot)}`
    : "";

  const iframeSnippet = selectedSlot
    ? `<!-- Bharattech Ads — Slot: ${selectedSlot} -->
<iframe
  src="${serveUrl}"
  width="${currentSlot?.width || 300}"
  height="${currentSlot?.height || 250}"
  frameborder="0"
  scrolling="no"
  style="border:none;overflow:hidden;"
></iframe>`
    : "Select a slot first";

  const scriptSnippet = selectedSlot
    ? `<!-- Bharattech Ads — Slot: ${selectedSlot} -->
<div id="oas-slot-${selectedSlot}"></div>
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

  const loadAd = async () => {
    if (!selectedSlot) return;
    setLoading(true);
    setError(null);
    try {
      const response = await requestAds({
        slot_id: selectedSlot,
        device: { os: "web" },
        num_ads: 1,
      });
      const nextAd = response.ads[0] ?? null;
      setAd(nextAd);
      setRequestId(response.request_id);
      if (nextAd) new Image().src = nextAd.tracking.impression_url;
      if (!nextAd) setError("No eligible ad was returned for this slot.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not request an ad");
    } finally {
      setLoading(false);
    }
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Clipboard access is unavailable.");
    }
  };

  return (
    <div>
      <h1>Integration</h1>
      <p className="muted">
        Signed in as {session?.user.name}. Select an ad slot below to generate
        embed code.
      </p>

      {/* Slot Selector */}
      <div className="card">
        <h2>Select Ad Slot</h2>
        {slots.length === 0 ? (
          <p className="muted">
            No ad slots found. Create one from the Publisher dashboard first.
          </p>
        ) : (
          <select
            value={selectedSlot}
            onChange={(e) => {
              setSelectedSlot(e.target.value);
              setAd(null);
              setRequestId("");
              setError(null);
            }}
            style={{
              width: "100%",
              padding: "8px 12px",
              borderRadius: "6px",
              border: "1px solid #444",
              background: "#1a1a2e",
              color: "#e0e0e0",
              fontSize: "14px",
            }}
          >
            {slots.map((slot) => (
              <option key={slot.slot_id} value={slot.slot_id}>
                {slot.name} — {slot.slot_id} ({slot.width}×{slot.height})
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Live Preview */}
      {selectedSlot && (
        <div className="card">
          <h2>Live Preview</h2>
          <p className="muted">
            This iframe loads the ad directly from the backend serve endpoint.
          </p>
          <div
            style={{
              display: "flex",
              justifyContent: "center",
              padding: "16px",
              background: "#111",
              borderRadius: "8px",
              marginTop: "12px",
            }}
          >
            <iframe
              src={serveUrl}
              width={currentSlot?.width || 300}
              height={currentSlot?.height || 250}
              frameBorder="0"
              scrolling="no"
              style={{ border: "1px solid #333", borderRadius: "4px" }}
              title="Ad Preview"
            />
          </div>
        </div>
      )}

      {/* JSON Ad Preview */}
      <div className="card">
        <div className="actions">
          <button
            className="btn"
            onClick={() => void loadAd()}
            disabled={loading || !selectedSlot}
          >
            {loading ? "Requesting..." : "Preview JSON response"}
          </button>
        </div>
        {error && <p className="error">{error}</p>}
        {ad && (
          <div className="ad-card">
            <div>
              {ad.creative.image_url && (
                <img
                  src={ad.creative.image_url}
                  alt={ad.creative.title ?? "Advertisement"}
                  width={ad.creative.width}
                  height={ad.creative.height}
                />
              )}
            </div>
            <div className="ad-body">
              <div className="sponsored">
                Sponsored · {ad.creative.creative_type}
              </div>
              <div className="ad-title">{ad.creative.title}</div>
              {ad.creative.description && (
                <div className="ad-desc">{ad.creative.description}</div>
              )}
              <a
                className="btn small"
                href={ad.creative.landing_url}
                target="_blank"
                rel="noreferrer"
                onClick={() => {
                  new Image().src = ad.tracking.click_url;
                }}
              >
                Open landing page
              </a>
            </div>
          </div>
        )}
        {requestId && <p className="muted">Request ID: {requestId}</p>}
      </div>

      {/* Embed Code — iframe */}
      <div className="card">
        <h2>Embed Code (iframe)</h2>
        <p className="muted">
          Copy and paste this code into your website where the ad should appear.
        </p>
        <pre style={{ position: "relative" }}>
          {iframeSnippet}
          <button
            className="btn secondary small"
            onClick={() => void copy(iframeSnippet)}
            style={{ position: "absolute", top: "8px", right: "8px" }}
          >
            {copied ? "Copied!" : "Copy"}
          </button>
        </pre>
      </div>

      {/* Embed Code — script tag */}
      <div className="card">
        <h2>Embed Code (script)</h2>
        <p className="muted">
          Alternative embed method using a script tag for dynamic loading.
        </p>
        <pre style={{ position: "relative" }}>
          {scriptSnippet}
          <button
            className="btn secondary small"
            onClick={() => void copy(scriptSnippet)}
            style={{ position: "absolute", top: "8px", right: "8px" }}
          >
            {copied ? "Copied!" : "Copy"}
          </button>
        </pre>
      </div>
    </div>
  );
}
