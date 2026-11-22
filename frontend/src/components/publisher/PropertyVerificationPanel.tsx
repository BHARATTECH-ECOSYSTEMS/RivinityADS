import React, { useState } from "react";
import { Link } from "react-router-dom";
import {
  challengePublisherProperty,
  verifyPublisherProperty,
  type PublisherProperty,
  type PropertyChallengeResponse,
} from "../../api/client";
import { Button, Alert } from "../ui";

export interface PropertyVerificationPanelProps {
  property: {
    id: number;
    name?: string;
    domain: string;
    verification_status?: string;
    verification_method?: string;
    verification_token?: string | null;
    challenge_expires_at?: string | null;
    verified_at?: string | null;
    last_error?: string | null;
  };
  initialChallenge?: PropertyChallengeResponse | null;
  onVerified?: (property: PublisherProperty) => void;
  onChallengeUpdated?: (challenge: PropertyChallengeResponse) => void;
  showSuccessBanner?: boolean;
  compact?: boolean;
  showCreateSlotAction?: boolean;
  onClose?: () => void;
}

// Helper to sanitize backend stack traces/IPs from user display
export const sanitizeErrorMessage = (msg: string): string => {
  return msg
    .replace(/https?:\/\/[0-9.:]+/g, "[internal endpoint]")
    .replace(/Traceback \(most recent call last\):[\s\S]*/i, "")
    .trim();
};

export const PropertyVerificationPanel: React.FC<PropertyVerificationPanelProps> = ({
  property,
  initialChallenge = null,
  onVerified,
  onChallengeUpdated,
  showSuccessBanner = true,
  compact = false,
  showCreateSlotAction = true,
  onClose,
}) => {
  const [verificationMethod, setVerificationMethod] = useState<"DNS_TXT" | "HTML_META">(
    property.verification_method === "HTML_META" ? "HTML_META" : "DNS_TXT"
  );

  // Synthesize initial challenge data if token already exists on the property
  const [challengeData, setChallengeData] = useState<PropertyChallengeResponse | null>(() => {
    if (initialChallenge) return initialChallenge;
    if (property.verification_token) {
      const recName = `_rivinity-challenge.${property.domain}`;
      const metaTag = `<meta name="rivinity-site-verification" content="${property.verification_token}">`;
      return {
        property_id: property.id,
        domain: property.domain,
        verification_method: (property.verification_method as any) || "DNS_TXT",
        verification_token: property.verification_token,
        dns_record_name: recName,
        dns_record_value: property.verification_token,
        html_meta_tag: metaTag,
        challenge_expires_at: property.challenge_expires_at || new Date(Date.now() + 48 * 3600 * 1000).toISOString(),
        instructions:
          property.verification_method === "HTML_META"
            ? `Add the meta tag '${metaTag}' into the <head> section of your website homepage.`
            : `Add a DNS TXT record for '${recName}' with value '${property.verification_token}'.`,
      };
    }
    return null;
  });

  const [isGeneratingChallenge, setIsGeneratingChallenge] = useState(false);
  const [isCheckingVerification, setIsCheckingVerification] = useState(false);
  const [verificationError, setVerificationError] = useState<string | null>(() => {
    return property.last_error ? sanitizeErrorMessage(property.last_error) : null;
  });
  const [verificationSuccess, setVerificationSuccess] = useState<boolean>(
    property.verification_status === "VERIFIED"
  );
  const [verifiedAt, setVerifiedAt] = useState<string | null>(property.verified_at || null);
  const [copiedField, setCopiedField] = useState<string | null>(null);

  const isCollisionError = Boolean(
    verificationError &&
      (verificationError.toLowerCase().includes("another publisher") ||
        verificationError.toLowerCase().includes("dispute") ||
        verificationError.toLowerCase().includes("collision"))
  );

  const copyToClipboard = async (text: string, fieldId: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedField(fieldId);
      setTimeout(() => setCopiedField(null), 2500);
    } catch {
      // Fallback if clipboard API unavailable
    }
  };

  const handleGenerateChallenge = async () => {
    setIsGeneratingChallenge(true);
    setVerificationError(null);
    try {
      const res = await challengePublisherProperty(property.id, { method: verificationMethod });
      setChallengeData(res);
      onChallengeUpdated?.(res);
    } catch (err: any) {
      const msg = err?.message || "Failed to generate challenge. Please try again.";
      setVerificationError(sanitizeErrorMessage(msg));
    } finally {
      setIsGeneratingChallenge(false);
    }
  };

  const handleCheckVerification = async () => {
    setIsCheckingVerification(true);
    setVerificationError(null);
    try {
      const updated = await verifyPublisherProperty(property.id);
      if (updated.verification_status === "VERIFIED") {
        setVerificationSuccess(true);
        setVerifiedAt(updated.verified_at || new Date().toISOString());
        setVerificationError(null);
        onVerified?.(updated);
      } else {
        setVerificationSuccess(false);
        const msg =
          updated.last_error ||
          "Verification check did not pass. Please verify your DNS record or HTML meta tag and try again.";
        setVerificationError(sanitizeErrorMessage(msg));
      }
    } catch (err: any) {
      setVerificationSuccess(false);
      const msg = err?.message || "Verification check encountered an error. Please retry shortly.";
      setVerificationError(sanitizeErrorMessage(msg));
    } finally {
      setIsCheckingVerification(false);
    }
  };



  return (
    <div className="property-verification-panel" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {/* Verification Feedback Banner */}
      {verificationSuccess && showSuccessBanner && (
        <div
          style={{
            background: "rgba(16, 185, 129, 0.1)",
            border: "1px solid rgba(16, 185, 129, 0.3)",
            borderRadius: "var(--radius-md, 8px)",
            padding: compact ? "12px" : "16px",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              fontWeight: 700,
              color: "var(--color-success, #10b981)",
            }}
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
                clipRule="evenodd"
              />
            </svg>
            Domain Ownership Confirmed!
          </div>
          <p
            style={{
              margin: "6px 0 12px",
              fontSize: "0.875rem",
              color: "var(--color-text-secondary, #6b7280)",
            }}
          >
            <strong>{property.domain}</strong> has been cryptographically verified and authorized for production ad
            serving.
            {verifiedAt && ` (Verified on ${new Date(verifiedAt).toLocaleString()})`}
          </p>

          {showCreateSlotAction && (
            <div
              style={{
                display: "flex",
                gap: "10px",
                flexWrap: "wrap",
                borderTop: "1px solid rgba(16, 185, 129, 0.2)",
                paddingTop: "12px",
              }}
            >
              <Link
                to={`/publisher/ad-units?property_id=${property.id}`}
                className="btn btn-primary btn-sm"
                onClick={onClose}
              >
                + Create Placement Slot →
              </Link>
              <Link to="/publisher/integration" className="btn btn-secondary btn-sm" onClick={onClose}>
                Integration Guide →
              </Link>
            </div>
          )}
        </div>
      )}

      {/* Error & Collision Alert */}
      {verificationError && (
        <div>
          <Alert variant="danger" title="Verification Check Failed">
            {verificationError}
          </Alert>
          {isCollisionError && (
            <div
              style={{
                marginTop: "8px",
                padding: "10px 12px",
                background: "rgba(239, 68, 68, 0.08)",
                border: "1px dashed rgba(239, 68, 68, 0.4)",
                borderRadius: "var(--radius-sm, 6px)",
                fontSize: "0.8125rem",
                color: "var(--color-text-secondary, #4b5563)",
              }}
            >
              <strong>Domain Ownership Dispute Guidance:</strong> This domain is already registered or verified under
              another publisher account. If you own this domain, please contact platform operations with proof of domain
              control (e.g., registrar screenshot or DNS zone authority) to request an administrative ownership transfer.
            </div>
          )}
        </div>
      )}

      {/* Method Selection */}
      <div>
        <label
          style={{
            fontWeight: 600,
            fontSize: "0.875rem",
            marginBottom: "8px",
            display: "block",
            color: "var(--color-text-primary, #111827)",
          }}
        >
          Verification Method:
        </label>
        <div style={{ display: "flex", gap: "12px" }}>
          <label
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              gap: "8px",
              padding: "10px 12px",
              borderRadius: "var(--radius-sm, 6px)",
              border: `1px solid ${
                verificationMethod === "DNS_TXT" ? "var(--color-primary, #3b82f6)" : "var(--border-color, #e5e7eb)"
              }`,
              background:
                verificationMethod === "DNS_TXT"
                  ? "var(--color-primary-subtle, rgba(59, 130, 246, 0.08))"
                  : "var(--color-bg-subtle, #f9fafb)",
              cursor: "pointer",
            }}
          >
            <input
              type="radio"
              name={`verMethod-${property.id}`}
              value="DNS_TXT"
              checked={verificationMethod === "DNS_TXT"}
              onChange={() => setVerificationMethod("DNS_TXT")}
              data-testid="challenge-method-dns"
            />
            <div style={{ fontSize: "0.8125rem" }}>
              <div style={{ fontWeight: 600 }}>DNS TXT Record</div>
              <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #9ca3af)" }}>
                Recommended for root domains &amp; apex zones
              </div>
            </div>
          </label>

          <label
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              gap: "8px",
              padding: "10px 12px",
              borderRadius: "var(--radius-sm, 6px)",
              border: `1px solid ${
                verificationMethod === "HTML_META" ? "var(--color-primary, #3b82f6)" : "var(--border-color, #e5e7eb)"
              }`,
              background:
                verificationMethod === "HTML_META"
                  ? "var(--color-primary-subtle, rgba(59, 130, 246, 0.08))"
                  : "var(--color-bg-subtle, #f9fafb)",
              cursor: "pointer",
            }}
          >
            <input
              type="radio"
              name={`verMethod-${property.id}`}
              value="HTML_META"
              checked={verificationMethod === "HTML_META"}
              onChange={() => setVerificationMethod("HTML_META")}
              data-testid="challenge-method-html"
            />
            <div style={{ fontSize: "0.8125rem" }}>
              <div style={{ fontWeight: 600 }}>HTML Meta Tag</div>
              <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #9ca3af)" }}>
                Fastest verification for blogs &amp; SPAs
              </div>
            </div>
          </label>
        </div>
      </div>

      {/* Challenge Action Bar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          background: "var(--color-bg-subtle, #f9fafb)",
          padding: "12px",
          borderRadius: "var(--radius-md, 8px)",
          border: "1px solid var(--border-color, #e5e7eb)",
        }}
      >
        <div>
          <div style={{ fontWeight: 600, fontSize: "0.8125rem" }}>
            {challengeData ? "Cryptographic Challenge Active" : "Ready to Generate Challenge"}
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #9ca3af)", marginTop: "2px" }}>
            {challengeData?.challenge_expires_at ? (
              `Expires: ${new Date(challengeData.challenge_expires_at).toLocaleString()} (48h validity)`
            ) : (
              "Issues an unpredictable token to prove domain control."
            )}
          </div>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={handleGenerateChallenge}
          isLoading={isGeneratingChallenge}
          disabled={isCheckingVerification}
          data-testid="generate-challenge-btn"
        >
          {challengeData ? "Renew Challenge" : "Generate Challenge"}
        </Button>
      </div>

      {/* Challenge Details Box */}
      {challengeData && (
        <div
          data-testid="challenge-details-box"
          style={{
            background: "var(--color-bg-surface, #ffffff)",
            border: "1px solid var(--border-color, #e5e7eb)",
            borderRadius: "var(--radius-md, 8px)",
            padding: "16px",
          }}
        >
          <div
            style={{
              fontWeight: 600,
              fontSize: "0.875rem",
              marginBottom: "12px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <span>Configuration Instructions</span>
            <span style={{ fontSize: "0.75rem", fontWeight: 400, color: "var(--color-text-muted, #9ca3af)" }}>
              Target: <strong>{property.domain}</strong>
            </span>
          </div>

          {challengeData.instructions && (
            <p style={{ margin: "0 0 12px 0", fontSize: "0.8125rem", color: "var(--color-text-secondary, #4b5563)" }}>
              {challengeData.instructions}
            </p>
          )}

          {verificationMethod === "DNS_TXT" ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <p style={{ margin: 0, fontSize: "0.8125rem", color: "var(--color-text-secondary, #4b5563)" }}>
                Add the following TXT record to your domain's DNS settings (e.g., Cloudflare, Route53, Namecheap):
              </p>

              <div>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: "0.75rem",
                    fontWeight: 600,
                    color: "var(--color-text-muted, #6b7280)",
                    marginBottom: "4px",
                  }}
                >
                  <span>RECORD HOST / NAME:</span>
                  <button
                    type="button"
                    onClick={() => copyToClipboard(challengeData.dns_record_name, "dns_name")}
                    style={{
                      background: "none",
                      border: "none",
                      color: "var(--color-primary, #3b82f6)",
                      cursor: "pointer",
                      fontSize: "0.75rem",
                      padding: 0,
                    }}
                  >
                    {copiedField === "dns_name" ? "✓ Copied!" : "Copy Host"}
                  </button>
                </div>
                <code
                  style={{
                    display: "block",
                    padding: "8px 12px",
                    background: "var(--color-bg-subtle, #f9fafb)",
                    borderRadius: "var(--radius-sm, 6px)",
                    fontSize: "0.8125rem",
                    fontFamily: "var(--font-mono, monospace)",
                    border: "1px solid var(--border-color, #e5e7eb)",
                    wordBreak: "break-all",
                  }}
                >
                  {challengeData.dns_record_name}
                </code>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "90px 1fr", gap: "8px" }}>
                <div>
                  <span
                    style={{
                      fontSize: "0.75rem",
                      fontWeight: 600,
                      color: "var(--color-text-muted, #6b7280)",
                      marginBottom: "4px",
                      display: "block",
                    }}
                  >
                    TYPE:
                  </span>
                  <code
                    style={{
                      display: "block",
                      padding: "8px 12px",
                      background: "var(--color-bg-subtle, #f9fafb)",
                      borderRadius: "var(--radius-sm, 6px)",
                      fontSize: "0.8125rem",
                      fontFamily: "var(--font-mono, monospace)",
                      border: "1px solid var(--border-color, #e5e7eb)",
                      textAlign: "center",
                    }}
                  >
                    TXT
                  </code>
                </div>
                <div>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: "0.75rem",
                      fontWeight: 600,
                      color: "var(--color-text-muted, #6b7280)",
                      marginBottom: "4px",
                    }}
                  >
                    <span>RECORD VALUE:</span>
                    <button
                      type="button"
                      onClick={() =>
                        copyToClipboard(
                          challengeData.dns_record_value || challengeData.verification_token,
                          "dns_val"
                        )
                      }
                      style={{
                        background: "none",
                        border: "none",
                        color: "var(--color-primary, #3b82f6)",
                        cursor: "pointer",
                        fontSize: "0.75rem",
                        padding: 0,
                      }}
                    >
                      {copiedField === "dns_val" ? "✓ Copied!" : "Copy Value"}
                    </button>
                  </div>
                  <code
                    style={{
                      display: "block",
                      padding: "8px 12px",
                      background: "var(--color-bg-subtle, #f9fafb)",
                      borderRadius: "var(--radius-sm, 6px)",
                      fontSize: "0.8125rem",
                      fontFamily: "var(--font-mono, monospace)",
                      border: "1px solid var(--border-color, #e5e7eb)",
                      wordBreak: "break-all",
                    }}
                  >
                    {challengeData.dns_record_value || challengeData.verification_token}
                  </code>
                </div>
              </div>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <p style={{ margin: 0, fontSize: "0.8125rem", color: "var(--color-text-secondary, #4b5563)" }}>
                Paste this &lt;meta&gt; tag inside the <code>&lt;head&gt;</code> element of your website homepage (
                <code>https://{property.domain}/</code>):
              </p>
              <div>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: "0.75rem",
                    fontWeight: 600,
                    color: "var(--color-text-muted, #6b7280)",
                    marginBottom: "4px",
                  }}
                >
                  <span>HTML META TAG:</span>
                  <button
                    type="button"
                    onClick={() =>
                      copyToClipboard(
                        challengeData.html_meta_tag ||
                          `<meta name="rivinity-site-verification" content="${challengeData.verification_token}">`,
                        "meta_tag"
                      )
                    }
                    style={{
                      background: "none",
                      border: "none",
                      color: "var(--color-primary, #3b82f6)",
                      cursor: "pointer",
                      fontSize: "0.75rem",
                      padding: 0,
                    }}
                  >
                    {copiedField === "meta_tag" ? "✓ Copied!" : "Copy Tag"}
                  </button>
                </div>
                <pre
                  style={{
                    margin: 0,
                    padding: "10px 12px",
                    background: "var(--color-bg-subtle, #f9fafb)",
                    borderRadius: "var(--radius-sm, 6px)",
                    fontSize: "0.8125rem",
                    fontFamily: "var(--font-mono, monospace)",
                    border: "1px solid var(--border-color, #e5e7eb)",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                  }}
                >
                  {challengeData.html_meta_tag ||
                    `<meta name="rivinity-site-verification" content="${challengeData.verification_token}">`}
                </pre>
              </div>
            </div>
          )}

          <div
            style={{
              fontSize: "0.75rem",
              color: "var(--color-text-muted, #9ca3af)",
              marginTop: "12px",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
          >
            <span>ℹ️</span>
            <span>
              DNS propagation typically takes 1 to 15 minutes. HTML meta tags are checked immediately upon saving.
            </span>
          </div>
        </div>
      )}

      {/* Verify Trigger Button */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          paddingTop: "4px",
        }}
      >
        <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #9ca3af)" }}>
          {challengeData
            ? "Once published, click below to verify ownership."
            : "Generate challenge to receive verification token."}
        </span>
        <Button
          variant="primary"
          onClick={handleCheckVerification}
          isLoading={isCheckingVerification}
          disabled={!challengeData && !property.verification_token}
          data-testid="verify-domain-btn"
        >
          {isCheckingVerification ? "Verifying Domain..." : "Verify Ownership Now"}
        </Button>
      </div>
    </div>
  );
};
