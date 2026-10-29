import { useState, useEffect } from "react";
import { previewMarketingCampaign } from "../../../api/client";
import type { CampaignPreview, EmailMarketingCampaign } from "../../../types";

interface CampaignPreviewModalProps {
  isOpen: boolean;
  campaign: EmailMarketingCampaign | null;
  onClose: () => void;
  onOpenTestSend?: () => void;
  onOpenLaunch?: () => void;
}

export default function CampaignPreviewModal({
  isOpen,
  campaign,
  onClose,
  onOpenTestSend,
  onOpenLaunch,
}: CampaignPreviewModalProps) {
  const [preview, setPreview] = useState<CampaignPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"rendered" | "raw">("rendered");

  useEffect(() => {
    if (!isOpen || !campaign) return;
    let active = true;
    setLoading(true);
    setError(null);

    previewMarketingCampaign(campaign.id)
      .then((data) => {
        if (active) setPreview(data);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "Failed to load campaign preview.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [isOpen, campaign]);

  if (!isOpen || !campaign) return null;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="preview-modal-title" data-testid="campaign-preview-modal">
      <div className="modal-dialog" style={{ maxWidth: "680px" }}>
        <div className="modal-header">
          <h3 id="preview-modal-title" className="modal-title">
            Preview Campaign: {campaign.name}
          </h3>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close dialog">
            <svg viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <div className="modal-body" style={{ maxHeight: "70vh", overflowY: "auto" }}>
          {error && (
            <div role="alert" style={{ background: "rgba(239, 68, 68, 0.1)", color: "#b91c1c", padding: "10px 14px", borderRadius: "4px", marginBottom: "14px", fontSize: "13px" }}>
              {error}
            </div>
          )}

          {loading ? (
            <div style={{ textAlign: "center", padding: "30px 0" }}>
              <p className="muted" style={{ margin: 0 }}>Generating live preview from server...</p>
            </div>
          ) : preview ? (
            <>
              {/* Audience & Metadata Bar */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "10px", background: "#f8fafc", padding: "12px", borderRadius: "6px", marginBottom: "14px", fontSize: "12px" }}>
                <div>
                  <span style={{ color: "#64748b", display: "block" }}>Subject</span>
                  <strong style={{ fontSize: "13px" }} data-testid="preview-subject">{preview.subject}</strong>
                </div>
                <div>
                  <span style={{ color: "#64748b", display: "block" }}>Eligible Audience</span>
                  <strong style={{ fontSize: "14px", color: "#16a34a" }} data-testid="preview-audience-count">
                    {preview.eligible_recipient_count} recipients
                  </strong>
                </div>
                <div>
                  <span style={{ color: "#64748b", display: "block" }}>Unsubscribe Notice</span>
                  <span
                    className="badge"
                    style={{
                      background: preview.has_unsubscribe_notice ? "#dcfce7" : "#fee2e2",
                      color: preview.has_unsubscribe_notice ? "#15803d" : "#b91c1c",
                      fontSize: "11px",
                      marginTop: "2px",
                      display: "inline-block",
                    }}
                    data-testid="preview-unsubscribe-status"
                  >
                    {preview.has_unsubscribe_notice ? "Verified Present" : "Missing"}
                  </span>
                </div>
              </div>

              {/* View Toggle */}
              <div style={{ display: "flex", gap: "8px", marginBottom: "10px" }}>
                <button
                  type="button"
                  className={`btn ${viewMode === "rendered" ? "primary" : "secondary"} small`}
                  onClick={() => setViewMode("rendered")}
                  data-testid="btn-preview-rendered"
                >
                  Rendered Preview
                </button>
                <button
                  type="button"
                  className={`btn ${viewMode === "raw" ? "primary" : "secondary"} small`}
                  onClick={() => setViewMode("raw")}
                  data-testid="btn-preview-raw"
                >
                  Raw Content
                </button>
              </div>

              {/* Preview Canvas */}
              {viewMode === "rendered" ? (
                <div
                  style={{
                    border: "1px solid #e2e8f0",
                    borderRadius: "6px",
                    padding: "16px",
                    background: "#ffffff",
                    minHeight: "220px",
                  }}
                  data-testid="rendered-preview-content"
                  dangerouslySetInnerHTML={{ __html: preview.rendered_body }}
                />
              ) : (
                <pre
                  style={{
                    border: "1px solid #e2e8f0",
                    borderRadius: "6px",
                    padding: "12px",
                    background: "#0f172a",
                    color: "#f8fafc",
                    fontFamily: "monospace",
                    fontSize: "12px",
                    whiteSpace: "pre-wrap",
                    maxHeight: "260px",
                    overflowY: "auto",
                  }}
                  data-testid="raw-preview-content"
                >
                  {preview.rendered_body}
                </pre>
              )}
            </>
          ) : null}
        </div>

        <div className="modal-footer" style={{ justifyContent: "space-between" }}>
          <div>
            {onOpenTestSend && (
              <button
                type="button"
                className="btn secondary small"
                onClick={() => {
                  onClose();
                  onOpenTestSend();
                }}
                data-testid="btn-proceed-test-send"
              >
                Test-Send Preview
              </button>
            )}
          </div>
          <div style={{ display: "flex", gap: "8px" }}>
            <button type="button" className="btn secondary small" onClick={onClose}>
              Close
            </button>
            {onOpenLaunch && campaign.status === "draft" && (
              <button
                type="button"
                className="btn primary small"
                onClick={() => {
                  onClose();
                  onOpenLaunch();
                }}
                data-testid="btn-proceed-launch"
              >
                Proceed to Launch...
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

