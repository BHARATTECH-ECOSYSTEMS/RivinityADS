import { useEffect, useRef, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { listSlots, requestAds, type SlotData } from "../../api/client";
import type { AdResponse } from "../../types";

const API_BASE =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? window.location.origin;

export default function AdPreview() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialSlot = searchParams.get("slot_id") ?? "";

  const [slots, setSlots] = useState<SlotData[]>([]);
  const [selectedSlot, setSelectedSlot] = useState(initialSlot);
  const [ad, setAd] = useState<AdResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [iframeLoading, setIframeLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [requestId, setRequestId] = useState("");
  const [impressionFired, setImpressionFired] = useState(false);
  const [clickTracked, setClickTracked] = useState(false);
  const [previewMode, setPreviewMode] = useState<"iframe" | "json">("iframe");
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    listSlots()
      .then((data) => {
        setSlots(data);
        if (!selectedSlot && data.length > 0) setSelectedSlot(data[0].slot_id);
      })
      .catch(() => {});
  }, []);

  const currentSlot = slots.find((s) => s.slot_id === selectedSlot);
  const serveUrl = selectedSlot
    ? `${API_BASE}/api/v1/ad/serve?slot_id=${encodeURIComponent(selectedSlot)}`
    : "";

  // Sync slot_id into URL query param
  const selectSlot = (slotId: string) => {
    setSelectedSlot(slotId);
    setSearchParams({ slot_id: slotId });
    setAd(null);
    setError(null);
    setImpressionFired(false);
    setClickTracked(false);
  };

  const fetchJsonAd = async () => {
    if (!selectedSlot) return;
    setLoading(true);
    setError(null);
    setAd(null);
    setImpressionFired(false);
    setClickTracked(false);
    try {
      const res = await requestAds({ slot_id: selectedSlot, device: { os: "web" }, num_ads: 1 });
      const winner = res.ads[0] ?? null;
      setAd(winner);
      setRequestId(res.request_id);
      if (winner) {
        new Image().src = winner.tracking.impression_url;
        setImpressionFired(true);
      } else {
        setError("No eligible ad returned. Make sure an active campaign with a creative exists.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch ad");
    } finally {
      setLoading(false);
    }
  };

  const refreshIframe = () => {
    if (iframeRef.current) {
      setIframeLoading(true);
      iframeRef.current.src = serveUrl + "&_t=" + Date.now();
    }
  };

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "16px" }}>
        <Link to="/publisher" className="btn secondary small">← Back</Link>
        <h1 style={{ margin: 0 }}>🔴 Live Ad Preview</h1>
      </div>
      <p className="muted">Watch your ad serving pipeline in real-time. Select a slot to preview the live ad.</p>

      {/* Slot Selector */}
      <div className="card">
        <h2>Select Ad Slot</h2>
        {slots.length === 0 ? (
          <p className="muted">
            No slots found.{" "}
            <Link to="/publisher" style={{ color: "#2563eb" }}>Create one from your dashboard first.</Link>
          </p>
        ) : (
          <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
            {slots.map((slot) => (
              <button
                key={slot.slot_id}
                onClick={() => selectSlot(slot.slot_id)}
                style={{
                  padding: "10px 16px",
                  borderRadius: "8px",
                  border: selectedSlot === slot.slot_id ? "2px solid #2563eb" : "1px solid #e2e8f0",
                  background: selectedSlot === slot.slot_id ? "#eff6ff" : "#f8fafc",
                  cursor: "pointer",
                  fontSize: "13px",
                  fontFamily: "inherit",
                  fontWeight: selectedSlot === slot.slot_id ? 700 : 400,
                  color: selectedSlot === slot.slot_id ? "#1d4ed8" : "#0f172a",
                  transition: "all .15s",
                }}
              >
                <div>{slot.name}</div>
                <div style={{ fontSize: "11px", color: "#64748b", marginTop: "2px" }}>
                  {slot.slot_id} · {slot.width}×{slot.height}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {selectedSlot && (
        <>
          {/* Preview Mode Toggle */}
          <div className="card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
              <h2 style={{ margin: 0 }}>Ad Preview</h2>
              <div style={{ display: "flex", gap: "8px" }}>
                <button
                  className={`btn ${previewMode === "iframe" ? "" : "secondary"} small`}
                  onClick={() => setPreviewMode("iframe")}
                >
                  iframe (Live)
                </button>
                <button
                  className={`btn ${previewMode === "json" ? "" : "secondary"} small`}
                  onClick={() => setPreviewMode("json")}
                >
                  JSON API
                </button>
              </div>
            </div>

            {/* ── iframe preview ── */}
            {previewMode === "iframe" && (
              <div>
                <p className="muted" style={{ marginBottom: "12px" }}>
                  This iframe loads the ad directly from{" "}
                  <code style={{ fontSize: "11px" }}>/api/v1/ad/serve?slot_id={selectedSlot}</code>
                  {" "}— exactly as your website visitors will see it.
                </p>

                {/* Device mock frame */}
                <div
                  style={{
                    background: "#0f172a",
                    borderRadius: "16px",
                    padding: "24px",
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    gap: "12px",
                  }}
                >
                  <div
                    style={{
                      background: "#1e293b",
                      borderRadius: "8px",
                      padding: "4px",
                      boxShadow: "0 0 0 1px #334155, 0 8px 24px rgba(0,0,0,.4)",
                      position: "relative",
                    }}
                  >
                    {iframeLoading && (
                      <div
                        style={{
                          position: "absolute",
                          inset: 0,
                          background: "rgba(15,23,42,.6)",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          borderRadius: "6px",
                          color: "#94a3b8",
                          fontSize: "13px",
                          zIndex: 2,
                        }}
                      >
                        Loading ad…
                      </div>
                    )}
                    <iframe
                      ref={iframeRef}
                      src={serveUrl}
                      width={currentSlot?.width ?? 300}
                      height={currentSlot?.height ?? 250}
                      frameBorder="0"
                      scrolling="no"
                      title="Live Ad Preview"
                      style={{ display: "block", borderRadius: "6px" }}
                      onLoad={() => setIframeLoading(false)}
                    />
                  </div>
                  <p style={{ color: "#64748b", fontSize: "12px", margin: 0 }}>
                    {currentSlot?.width ?? 300}×{currentSlot?.height ?? 250} · {currentSlot?.name}
                  </p>
                </div>

                <div style={{ marginTop: "16px", display: "flex", gap: "8px", flexWrap: "wrap" }}>
                  <button className="btn small" onClick={refreshIframe}>
                    🔄 Refresh Ad
                  </button>
                  <a
                    href={serveUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="btn secondary small"
                  >
                    ↗ Open in new tab
                  </a>
                </div>
              </div>
            )}

            {/* ── JSON API preview ── */}
            {previewMode === "json" && (
              <div>
                <p className="muted" style={{ marginBottom: "12px" }}>
                  Calls <code style={{ fontSize: "11px" }}>POST /api/v1/ad/request</code> and shows the raw JSON response with tracking URLs.
                </p>
                <button
                  className="btn"
                  onClick={() => void fetchJsonAd()}
                  disabled={loading}
                  style={{ marginBottom: "16px" }}
                >
                  {loading ? "Requesting ad…" : "🎯 Request Ad (JSON)"}
                </button>

                {error && (
                  <div
                    style={{
                      background: "#fef2f2",
                      border: "1px solid #fecaca",
                      borderRadius: "8px",
                      padding: "12px 16px",
                      color: "#b91c1c",
                      fontSize: "13px",
                    }}
                  >
                    ⚠️ {error}
                  </div>
                )}

                {ad && (
                  <div>
                    {/* Ad Card */}
                    <div className="ad-card" style={{ marginBottom: "16px", maxWidth: "400px" }}>
                      {ad.creative.image_url && (
                        <img
                          src={ad.creative.image_url}
                          alt={ad.creative.title ?? "Ad"}
                          style={{ width: "100%", height: "auto", display: "block", background: "#e2e8f0" }}
                        />
                      )}
                      <div className="ad-body">
                        <div className="sponsored">Sponsored · {ad.creative.creative_type}</div>
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
                            setClickTracked(true);
                          }}
                        >
                          Open landing page ↗
                        </a>
                      </div>
                    </div>

                    {/* Event Status */}
                    <div
                      style={{
                        display: "flex",
                        gap: "10px",
                        marginBottom: "16px",
                        flexWrap: "wrap",
                      }}
                    >
                      <span
                        style={{
                          padding: "4px 12px",
                          borderRadius: "999px",
                          fontSize: "12px",
                          fontWeight: 600,
                          background: impressionFired ? "#dcfce7" : "#f1f5f9",
                          color: impressionFired ? "#15803d" : "#64748b",
                        }}
                      >
                        {impressionFired ? "✅ Impression tracked" : "⏳ Impression pending"}
                      </span>
                      <span
                        style={{
                          padding: "4px 12px",
                          borderRadius: "999px",
                          fontSize: "12px",
                          fontWeight: 600,
                          background: clickTracked ? "#dcfce7" : "#f1f5f9",
                          color: clickTracked ? "#15803d" : "#64748b",
                        }}
                      >
                        {clickTracked ? "✅ Click tracked" : "⏳ Click pending (open landing page)"}
                      </span>
                    </div>

                    {/* Raw JSON */}
                    <pre style={{ fontSize: "11px", lineHeight: "1.6", maxHeight: "260px", overflowY: "auto" }}>
                      {JSON.stringify(ad, null, 2)}
                    </pre>
                    {requestId && (
                      <p className="muted" style={{ marginTop: "8px" }}>Request ID: {requestId}</p>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Embed Code quick-copy */}
          <div className="card">
            <h2>Quick Embed</h2>
            <p className="muted">Copy and paste this into your site where the ad should appear.</p>
            <pre style={{ fontSize: "11px" }}>
              {`<iframe\n  src="${serveUrl}"\n  width="${currentSlot?.width ?? 300}"\n  height="${currentSlot?.height ?? 250}"\n  frameborder="0" scrolling="no"\n  style="border:none;"\n></iframe>`}
            </pre>
            <p className="muted" style={{ marginTop: "8px" }}>
              Or go to <Link to="/publisher/integration" style={{ color: "#2563eb" }}>Integration</Link> for the full embed code + script tag.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
