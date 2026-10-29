import { useState, useEffect } from "react";
import { getCampaignAnalytics } from "../../../api/client";
import type { CampaignAnalytics, EmailMarketingCampaign } from "../../../types";

interface CampaignAnalyticsModalProps {
  isOpen: boolean;
  campaign: EmailMarketingCampaign | null;
  onClose: () => void;
}

export default function CampaignAnalyticsModal({
  isOpen,
  campaign,
  onClose,
}: CampaignAnalyticsModalProps) {
  const [stats, setStats] = useState<CampaignAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen || !campaign) return;
    let active = true;
    setLoading(true);
    setError(null);

    getCampaignAnalytics(campaign.id)
      .then((data) => {
        if (active) setStats(data);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "Failed to load campaign analytics.");
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
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="analytics-modal-title" data-testid="campaign-analytics-modal">
      <div className="modal-dialog" style={{ maxWidth: "600px" }}>
        <div className="modal-header">
          <h3 id="analytics-modal-title" className="modal-title">
            Campaign Analytics: {campaign.name}
          </h3>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close dialog">
            <svg viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <div className="modal-body">
          {error && (
            <div role="alert" style={{ background: "rgba(239, 68, 68, 0.1)", color: "#b91c1c", padding: "10px 14px", borderRadius: "4px", marginBottom: "14px", fontSize: "13px" }}>
              {error}
            </div>
          )}

          {loading ? (
            <div style={{ textAlign: "center", padding: "30px 0" }}>
              <p className="muted" style={{ margin: 0 }}>Fetching real analytics from provider...</p>
            </div>
          ) : stats ? (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px", marginBottom: "16px" }}>
                <div style={{ background: "#f8fafc", padding: "12px", borderRadius: "6px", textAlign: "center" }}>
                  <div style={{ fontSize: "11px", color: "#64748b", textTransform: "uppercase" }}>Total Recipients</div>
                  <div style={{ fontSize: "22px", fontWeight: 700, marginTop: "4px" }} data-testid="stat-recipients">
                    {stats.total_recipients ?? "Unavailable"}
                  </div>
                </div>

                <div style={{ background: "#ecfdf5", padding: "12px", borderRadius: "6px", textAlign: "center" }}>
                  <div style={{ fontSize: "11px", color: "#15803d", textTransform: "uppercase" }}>Views / Opens</div>
                  <div style={{ fontSize: "22px", fontWeight: 700, color: "#15803d", marginTop: "4px" }} data-testid="stat-views">
                    {stats.views ?? "Unavailable"}
                  </div>
                </div>

                <div style={{ background: "#eff6ff", padding: "12px", borderRadius: "6px", textAlign: "center" }}>
                  <div style={{ fontSize: "11px", color: "#1d4ed8", textTransform: "uppercase" }}>Clicks</div>
                  <div style={{ fontSize: "22px", fontWeight: 700, color: "#1d4ed8", marginTop: "4px" }} data-testid="stat-clicks">
                    {stats.clicks ?? "Unavailable"}
                  </div>
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "12px", marginBottom: "16px" }}>
                <div style={{ background: "#fef3c7", padding: "10px", borderRadius: "6px", textAlign: "center" }}>
                  <div style={{ fontSize: "11px", color: "#b45309", textTransform: "uppercase" }}>Unsubscribes</div>
                  <div style={{ fontSize: "18px", fontWeight: 700, color: "#b45309", marginTop: "4px" }} data-testid="stat-unsubscribes">
                    {stats.unsubscribes ?? "Unavailable"}
                  </div>
                </div>

                <div style={{ background: "#fef2f2", padding: "10px", borderRadius: "6px", textAlign: "center" }}>
                  <div style={{ fontSize: "11px", color: "#b91c1c", textTransform: "uppercase" }}>Bounces</div>
                  <div style={{ fontSize: "18px", fontWeight: 700, color: "#b91c1c", marginTop: "4px" }} data-testid="stat-bounces">
                    {stats.bounces ?? "Unavailable"}
                  </div>
                </div>
              </div>

              <p style={{ margin: 0, fontSize: "12px", color: "#64748b", textAlign: "center" }}>
                All metrics reflect actual telemetry from backend outbox and Listmonk. No synthetic statistics are presented.
              </p>
            </>
          ) : null}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn primary small" onClick={onClose} data-testid="btn-close-analytics">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

