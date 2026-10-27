import React, { useEffect, useRef, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
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

const API_BASE =
  (import.meta.env?.VITE_API_BASE_URL as string | undefined) ??
  (typeof window !== "undefined" ? window.location.origin : "http://localhost:5173");

export default function AdPreview() {
  const { session } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialSlot = searchParams.get("slot_id") ?? "";

  const [slots, setSlots] = useState<SlotData[]>([]);
  const [selectedSlot, setSelectedSlot] = useState(initialSlot);
  const [ad, setAd] = useState<AdResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [requestId, setRequestId] = useState("");
  const [impressionFired, setImpressionFired] = useState(false);
  const [clickTracked, setClickTracked] = useState(false);
  const [previewMode, setPreviewMode] = useState<"iframe" | "json">("iframe");
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const publisherId = session?.user?.publisher_id;
    listSlots(publisherId || undefined)
      .then((data) => {
        setSlots(data);
        if (!selectedSlot && data.length > 0) {
          setSelectedSlot(data[0].slot_id);
          setSearchParams({ slot_id: data[0].slot_id });
        }
      })
      .catch(() => {});
  }, [session?.user?.publisher_id]);

  const currentSlot = slots.find((s) => s.slot_id === selectedSlot);
  const serveUrl = selectedSlot
    ? `${API_BASE}/api/v1/ad/serve?slot_id=${encodeURIComponent(selectedSlot)}`
    : "";

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
      const res = await requestAds({
        slot_id: selectedSlot,
        num_ads: 1,
        user: {
          id: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          sector_context: ["developer"],
        },
        device: { os: "web" },
      });
      const winner = res.ads[0] ?? null;
      setAd(winner);
      setRequestId(res.request_id);
      if (winner) {
        new Image().src = winner.tracking.impression_url;
        setImpressionFired(true);
      } else {
        setError("No eligible ad returned. Ensure an active campaign targeting this format exists.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch ad");
    } finally {
      setLoading(false);
    }
  };

  const refreshIframe = () => {
    if (iframeRef.current) {
      iframeRef.current.src = serveUrl + "&_t=" + Date.now();
      setImpressionFired(true);
    }
  };

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Ad Delivery Telemetry"
        title="Live Ad Preview"
        description="Verify real-time ad serving, evaluate winner creative assets, and test telemetry trackers."
        actions={
          <Link to="/publisher/slots" className="btn btn-secondary btn-sm">
            Manage Ad Units
          </Link>
        }
      />

      {/* Slot Selector Card */}
      <Card
        title="Select Placement Slot"
        subtitle="Choose which ad unit to simulate in the preview container."
        style={{ marginBottom: "24px" }}
      >
        {slots.length === 0 ? (
          <EmptyState
            title="No ad units found"
            description="Create an ad unit from your publisher inventory first."
            action={
              <Link to="/publisher/slots" className="btn btn-primary btn-sm">
                Create Ad Unit
              </Link>
            }
          />
        ) : (
          <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
            {slots.map((slot) => {
              const isSelected = selectedSlot === slot.slot_id;
              return (
                <button
                  key={slot.slot_id}
                  type="button"
                  onClick={() => selectSlot(slot.slot_id)}
                  style={{
                    padding: "10px 16px",
                    borderRadius: "var(--radius-lg)",
                    border: isSelected ? "2px solid var(--color-primary)" : "1px solid var(--border-color)",
                    backgroundColor: isSelected ? "var(--color-primary-subtle)" : "var(--color-bg-surface)",
                    cursor: "pointer",
                    textAlign: "left",
                    transition: "all 0.15s ease",
                  }}
                >
                  <div style={{ fontWeight: 600, fontSize: "0.875rem", color: isSelected ? "var(--color-primary)" : "var(--color-text-primary)" }}>
                    {slot.name}
                  </div>
                  <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "2px", fontFamily: "var(--font-mono)" }}>
                    {slot.slot_id} · {slot.width}×{slot.height}px
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </Card>

      {selectedSlot && (
        <Card
          title={`Preview: ${currentSlot?.name || selectedSlot}`}
          subtitle={`${currentSlot?.width || 300}×${currentSlot?.height || 250}px · Format #${currentSlot?.format || 1}`}
          style={{ marginBottom: "24px" }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", borderBottom: "1px solid var(--border-color)", paddingBottom: "12px" }}>
            <div style={{ display: "flex", gap: "8px" }}>
              <button
                type="button"
                className={`btn ${previewMode === "iframe" ? "btn-primary" : "btn-secondary"} btn-sm`}
                onClick={() => setPreviewMode("iframe")}
              >
                Iframe (Live Sandbox)
              </button>
              <button
                type="button"
                className={`btn ${previewMode === "json" ? "btn-primary" : "btn-secondary"} btn-sm`}
                onClick={() => {
                  setPreviewMode("json");
                  if (!ad) void fetchJsonAd();
                }}
              >
                JSON Payload Inspector
              </button>
            </div>

            <div style={{ display: "flex", gap: "8px" }}>
              {previewMode === "iframe" ? (
                <Button variant="secondary" size="sm" onClick={refreshIframe}>
                  Reload Iframe
                </Button>
              ) : (
                <Button variant="secondary" size="sm" onClick={fetchJsonAd} isLoading={loading}>
                  Fetch Fresh Ad
                </Button>
              )}
            </div>
          </div>

          {error && (
            <Alert variant="warning" title="Serving Notice">
              {error}
            </Alert>
          )}

          {previewMode === "iframe" ? (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "24px", background: "var(--color-bg-subtle)", borderRadius: "var(--radius-lg)" }}>
              <div
                style={{
                  width: `${currentSlot?.width || 300}px`,
                  height: `${currentSlot?.height || 250}px`,
                  boxShadow: "var(--shadow-md)",
                  borderRadius: "var(--radius-md)",
                  overflow: "hidden",
                  backgroundColor: "#ffffff",
                  border: "1px dashed var(--border-color-strong)",
                }}
              >
                <iframe
                  ref={iframeRef}
                  src={serveUrl}
                  width={currentSlot?.width || 300}
                  height={currentSlot?.height || 250}
                  style={{ border: "none", overflow: "hidden", display: "block" }}
                  title="Live Ad Unit Preview"
                  onLoad={() => setImpressionFired(true)}
                />
              </div>
              <div style={{ marginTop: "12px", fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                Endpoint: <code style={{ fontFamily: "var(--font-mono)" }}>{serveUrl}</code>
              </div>
            </div>
          ) : (
            <div>
              {loading ? (
                <LoadingSkeleton variant="rect" height={150} />
              ) : ad ? (
                <div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", marginBottom: "16px" }}>
                    <div style={{ background: "var(--color-bg-subtle)", padding: "16px", borderRadius: "var(--radius-md)" }}>
                      <div style={{ fontWeight: 600, fontSize: "0.9375rem", marginBottom: "4px" }}>
                        {ad.creative.title}
                      </div>
                      <div style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)", marginBottom: "8px" }}>
                        {ad.creative.description || "No description provided."}
                      </div>
                      <div style={{ fontSize: "0.8125rem" }}>
                        Destination: <a href={ad.creative.landing_url} target="_blank" rel="noreferrer" style={{ color: "var(--color-primary)" }}>{ad.creative.landing_url}</a>
                      </div>
                    </div>

                    <div style={{ background: "var(--color-bg-subtle)", padding: "16px", borderRadius: "var(--radius-md)" }}>
                      <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", textTransform: "uppercase", fontWeight: 600 }}>Tracking Status</div>
                      <div style={{ display: "flex", gap: "12px", marginTop: "8px" }}>
                        <StatusBadge 
                          status={impressionFired ? "success" : "pending"} 
                          label={impressionFired ? "Impression Fired" : "Waiting for Impression"}
                          size="sm"
                        />
                        <StatusBadge 
                          status={clickTracked ? "success" : "pending"} 
                          label={clickTracked ? "Click Tracked" : "No Click Yet"}
                          size="sm"
                        />
                      </div>
                      {ad.tracking.click_url && (
                        <div style={{ marginTop: "12px" }}>
                          <a
                            href={ad.tracking.click_url}
                            target="_blank"
                            rel="noreferrer"
                            className="btn btn-secondary btn-sm"
                            onClick={() => setClickTracked(true)}
                          >
                            Simulate Ad Click ↗
                          </a>
                        </div>
                      )}
                    </div>
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
                    {JSON.stringify(ad, null, 2)}
                  </pre>
                </div>
              ) : null}
            </div>
          )}
        </Card>
      )}
    </PageContainer>
  );
}
