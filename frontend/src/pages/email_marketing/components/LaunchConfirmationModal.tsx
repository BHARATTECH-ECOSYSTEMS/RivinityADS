import { useState } from "react";
import { launchMarketingCampaign } from "../../../api/client";
import type { EmailMarketingCampaign } from "../../../types";

interface LaunchConfirmationModalProps {
  isOpen: boolean;
  campaign: EmailMarketingCampaign | null;
  targetListName?: string;
  onClose: () => void;
  onLaunched: (campaign: EmailMarketingCampaign) => void;
}

export default function LaunchConfirmationModal({
  isOpen,
  campaign,
  targetListName,
  onClose,
  onLaunched,
}: LaunchConfirmationModalProps) {
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unknownStatus, setUnknownStatus] = useState(false);

  if (!isOpen || !campaign) return null;

  const handleLaunch = async () => {
    if (!confirmed) return;
    setSubmitting(true);
    setError(null);
    setUnknownStatus(false);

    try {
      const launched = await launchMarketingCampaign(campaign.id, true);
      onLaunched(launched);
      onClose();
    } catch (err: any) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.toLowerCase().includes("timeout") || msg.toLowerCase().includes("network") || msg.toLowerCase().includes("504")) {
        setUnknownStatus(true);
      }
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="launch-confirm-title" data-testid="launch-confirm-modal">
      <div className="modal-dialog" style={{ maxWidth: "560px" }}>
        <div className="modal-header">
          <h3 id="launch-confirm-title" className="modal-title" style={{ color: "#b91c1c" }}>
            Final Review &amp; Launch Campaign
          </h3>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close dialog">
            <svg viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <div className="modal-body">
          {error && (
            <div
              role="alert"
              style={{
                background: unknownStatus ? "rgba(245, 158, 11, 0.1)" : "rgba(239, 68, 68, 0.1)",
                color: unknownStatus ? "#b45309" : "#b91c1c",
                padding: "10px 14px",
                borderRadius: "4px",
                marginBottom: "14px",
                fontSize: "13px",
              }}
              data-testid="launch-error-banner"
            >
              {unknownStatus ? (
                <div>
                  <strong>Status Unknown:</strong> The request timed out or was interrupted. Do not blindly retry to avoid duplicate sends. Please inspect the campaign status table.
                </div>
              ) : (
                <div><strong>Launch Error:</strong> {error}</div>
              )}
            </div>
          )}

          <p style={{ margin: "0 0 14px", fontSize: "13px", color: "#334155" }}>
            You are about to launch this marketing campaign. Once dispatched, emails will be delivered according to list subscriber preferences.
          </p>

          <div style={{ background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: "6px", padding: "12px", marginBottom: "14px", fontSize: "13px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "130px 1fr", rowGap: "8px" }}>
              <span style={{ color: "#64748b" }}>Campaign:</span>
              <strong data-testid="confirm-camp-name">{campaign.name}</strong>

              <span style={{ color: "#64748b" }}>Subject Line:</span>
              <span data-testid="confirm-camp-subject">{campaign.subject}</span>

              {targetListName && (
                <>
                  <span style={{ color: "#64748b" }}>Target List:</span>
                  <span>{targetListName}</span>
                </>
              )}

              <span style={{ color: "#64748b" }}>Unsubscribe Notice:</span>
              <span style={{ color: "#16a34a", fontWeight: 600 }}>Enforced Server-Side</span>

              <span style={{ color: "#64748b" }}>Audience Safety:</span>
              <span style={{ color: "#0369a1" }}>Suppressed/unconsented emails are excluded at dispatch</span>
            </div>
          </div>

          <div style={{ borderTop: "1px solid #e2e8f0", paddingTop: "12px" }}>
            <label style={{ display: "flex", alignItems: "flex-start", gap: "8px", fontSize: "12px", color: "#334155", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
                data-testid="checkbox-launch-confirm"
                style={{ marginTop: "2px" }}
              />
              <span>
                <strong>Explicit Launch Authorization:</strong> I have reviewed this campaign and confirm that all recipients provided affirmative consent. I authorize launching this campaign.
              </span>
            </label>
          </div>
        </div>

        <div className="modal-footer">
          <button type="button" className="btn secondary small" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button
            type="button"
            className="btn small"
            style={{ background: "#dc2626", color: "#ffffff", borderColor: "#dc2626" }}
            onClick={handleLaunch}
            disabled={submitting || !confirmed}
            data-testid="btn-confirm-launch"
          >
            {submitting ? "Launching..." : "Confirm & Launch Campaign"}
          </button>
        </div>
      </div>
    </div>
  );
}

