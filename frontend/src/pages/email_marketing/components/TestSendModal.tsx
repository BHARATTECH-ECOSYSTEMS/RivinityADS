import { useState, type FormEvent } from "react";
import { testSendMarketingCampaign } from "../../../api/client";
import type { EmailMarketingCampaign } from "../../../types";

interface TestSendModalProps {
  isOpen: boolean;
  campaign: EmailMarketingCampaign | null;
  onClose: () => void;
  onSuccess?: () => void;
}

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function TestSendModal({
  isOpen,
  campaign,
  onClose,
  onSuccess,
}: TestSendModalProps) {
  const [emailsText, setEmailsText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  if (!isOpen || !campaign) return null;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccessMsg(null);

    const emailList = emailsText
      .split(/[,\n]/)
      .map((e) => e.trim().toLowerCase())
      .filter((e) => e.length > 0);

    if (emailList.length === 0) {
      setError("Please provide at least one test email address.");
      return;
    }
    if (emailList.length > 5) {
      setError("Maximum 5 test recipients allowed by server safety policy.");
      return;
    }
    const invalid = emailList.filter((e) => !EMAIL_REGEX.test(e));
    if (invalid.length > 0) {
      setError(`Invalid email syntax: ${invalid.join(", ")}`);
      return;
    }

    setSubmitting(true);
    try {
      const res = await testSendMarketingCampaign(campaign.id, emailList);
      setSuccessMsg(res.message || "Test emails dispatched successfully.");
      if (onSuccess) onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to dispatch test emails.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="test-send-title" data-testid="test-send-modal">
      <div className="modal-dialog">
        <div className="modal-header">
          <h3 id="test-send-title" className="modal-title">
            Test-Send: {campaign.name}
          </h3>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close dialog">
            <svg viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && (
              <div role="alert" style={{ background: "rgba(239, 68, 68, 0.1)", color: "#b91c1c", padding: "8px 12px", borderRadius: "4px", marginBottom: "12px", fontSize: "13px" }}>
                {error}
              </div>
            )}
            {successMsg && (
              <div role="status" style={{ background: "rgba(22, 163, 74, 0.1)", color: "#15803d", padding: "8px 12px", borderRadius: "4px", marginBottom: "12px", fontSize: "13px" }}>
                {successMsg}
              </div>
            )}

            <p style={{ margin: "0 0 12px", fontSize: "13px", color: "#475569" }}>
              Send an exact rendered preview to team members or test mailboxes before public dispatch.
            </p>

            <div style={{ marginBottom: "12px" }}>
              <label htmlFor="test-recipients" style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                Test Email Addresses (Max 5, separated by commas or lines) *
              </label>
              <textarea
                id="test-recipients"
                className="input"
                rows={3}
                style={{ width: "100%", padding: "8px", boxSizing: "border-box", fontFamily: "monospace", fontSize: "12px" }}
                placeholder="developer@myorg.com, qa@myorg.com"
                value={emailsText}
                onChange={(e) => setEmailsText(e.target.value)}
                required
                data-testid="textarea-test-emails"
              />
              <span style={{ fontSize: "11px", color: "#64748b" }}>
                Server constraint: Enforces allowlisted recipients and maximum 5 addresses per test-send.
              </span>
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn secondary small" onClick={onClose} disabled={submitting}>
              {successMsg ? "Done" : "Cancel"}
            </button>
            <button
              type="submit"
              className="btn primary small"
              disabled={submitting || !emailsText.trim()}
              data-testid="submit-test-send"
            >
              {submitting ? "Sending..." : "Send Test Emails"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

