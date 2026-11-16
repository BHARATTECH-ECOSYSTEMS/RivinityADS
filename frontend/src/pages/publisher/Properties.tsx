import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { 
  listPublisherProperties, 
  createPublisherProperty, 
  updatePublisherProperty, 
  challengePublisherProperty,
  verifyPublisherProperty,
  type PublisherProperty,
  type PropertyChallengeResponse,
  listSlots,
  type SlotData
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { 
  PageContainer, 
  PageHeader, 
  Card, 
  Button, 
  StatusBadge, 
  Alert, 
  LoadingSkeleton, 
  EmptyState,
  ConfirmDialog
} from "../../components/ui";

export default function PublisherProperties() {
  const { session } = useAuth();
  const [properties, setProperties] = useState<PublisherProperty[]>([]);
  const [slots, setSlots] = useState<SlotData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formFeedback, setFormFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // Status toggle confirmation
  const [pendingToggle, setPendingToggle] = useState<{ id: number; currentStatus: number; name: string } | null>(null);
  const [toggleLoading, setToggleLoading] = useState(false);

  // Verification modal state
  const [verifyModalProperty, setVerifyModalProperty] = useState<PublisherProperty | null>(null);
  const [verificationMethod, setVerificationMethod] = useState<"DNS_TXT" | "HTML_META">("DNS_TXT");
  const [challengeData, setChallengeData] = useState<PropertyChallengeResponse | null>(null);
  const [isGeneratingChallenge, setIsGeneratingChallenge] = useState(false);
  const [isCheckingVerification, setIsCheckingVerification] = useState(false);
  const [verificationError, setVerificationError] = useState<string | null>(null);
  const [verificationSuccess, setVerificationSuccess] = useState(false);
  const [copiedField, setCopiedField] = useState<string | null>(null);

  const publisherId = session?.user?.publisher_id;

  const loadData = async () => {
    if (!publisherId) return;
    try {
      setLoading(true);
      const [propsData, slotsData] = await Promise.all([
        listPublisherProperties(publisherId),
        listSlots(publisherId)
      ]);
      setProperties(propsData);
      setSlots(slotsData);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load properties");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { 
    void loadData(); 
  }, [publisherId]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!publisherId) return;
    setSubmitting(true);
    setFormFeedback(null);
    try {
      // Clean domain formatting
      const cleanDomain = domain.trim().replace(/^https?:\/\//i, "").replace(/\/.*$/, "");
      const created = await createPublisherProperty(publisherId, { name: name.trim(), domain: cleanDomain });
      setName("");
      setDomain("");
      setFormFeedback({ 
        type: "success", 
        message: `Property "${cleanDomain}" added successfully. Please complete domain verification below to enable ad delivery.` 
      });
      await loadData();
      // Automatically open verification modal for newly registered domain
      openVerifyModal(created);
    } catch (err: any) {
      setFormFeedback({ type: "error", message: err.message || "Failed to create property" });
    } finally {
      setSubmitting(false);
    }
  };

  const confirmToggleStatus = async () => {
    if (!pendingToggle) return;
    setToggleLoading(true);
    try {
      const nextStatus = pendingToggle.currentStatus === 1 ? 0 : 1;
      await updatePublisherProperty(pendingToggle.id, { status: nextStatus });
      setPendingToggle(null);
      await loadData();
    } catch (err: any) {
      setError(err.message || "Failed to update property status");
    } finally {
      setToggleLoading(false);
    }
  };

  // Verification modal handlers
  const openVerifyModal = (property: PublisherProperty) => {
    setVerifyModalProperty(property);
    setVerificationMethod((property.verification_method as "DNS_TXT" | "HTML_META") || "DNS_TXT");
    setVerificationError(property.last_error || null);
    setVerificationSuccess(property.verification_status === "VERIFIED");
    setCopiedField(null);

    // If property has an existing active token, synthesize initial challenge display
    if (property.verification_token) {
      const recName = `_rivinity-challenge.${property.domain}`;
      const metaTag = `<meta name="rivinity-site-verification" content="${property.verification_token}">`;
      setChallengeData({
        property_id: property.id,
        domain: property.domain,
        verification_method: property.verification_method,
        verification_token: property.verification_token,
        dns_record_name: recName,
        dns_record_value: property.verification_token,
        html_meta_tag: metaTag,
        challenge_expires_at: property.challenge_expires_at || new Date(Date.now() + 48 * 3600 * 1000).toISOString(),
        instructions: property.verification_method === "HTML_META"
          ? `Add the meta tag '${metaTag}' into the <head> section of your website homepage.`
          : `Add a DNS TXT record for '${recName}' with value '${property.verification_token}'.`,
      });
    } else {
      setChallengeData(null);
    }
  };

  const closeVerifyModal = () => {
    setVerifyModalProperty(null);
    setChallengeData(null);
    setVerificationError(null);
    setVerificationSuccess(false);
  };

  const handleGenerateChallenge = async () => {
    if (!verifyModalProperty) return;
    setIsGeneratingChallenge(true);
    setVerificationError(null);
    try {
      const res = await challengePublisherProperty(verifyModalProperty.id, { method: verificationMethod });
      setChallengeData(res);
      // Update property in local state
      setProperties(prev => prev.map(p => p.id === verifyModalProperty.id ? {
        ...p,
        verification_method: res.verification_method,
        verification_token: res.verification_token,
        challenge_expires_at: res.challenge_expires_at,
        verification_status: "PENDING",
        last_error: null,
      } : p));
      setVerifyModalProperty(prev => prev ? {
        ...prev,
        verification_method: res.verification_method,
        verification_token: res.verification_token,
        challenge_expires_at: res.challenge_expires_at,
        verification_status: "PENDING",
        last_error: null,
      } : null);
    } catch (err: any) {
      setVerificationError(err.message || "Failed to generate challenge. Please try again.");
    } finally {
      setIsGeneratingChallenge(false);
    }
  };

  const handleCheckVerification = async () => {
    if (!verifyModalProperty) return;
    setIsCheckingVerification(true);
    setVerificationError(null);
    try {
      const updated = await verifyPublisherProperty(verifyModalProperty.id);
      setVerifyModalProperty(updated);
      setProperties(prev => prev.map(p => p.id === updated.id ? updated : p));
      
      if (updated.verification_status === "VERIFIED") {
        setVerificationSuccess(true);
        setVerificationError(null);
      } else {
        setVerificationSuccess(false);
        setVerificationError(updated.last_error || "Verification failed. Please verify your DNS record or HTML meta tag and try again.");
      }
    } catch (err: any) {
      setVerificationSuccess(false);
      setVerificationError(err.message || "Verification check encountered an error. Please retry shortly.");
    } finally {
      setIsCheckingVerification(false);
    }
  };

  const copyToClipboard = async (text: string, fieldId: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedField(fieldId);
      setTimeout(() => setCopiedField(null), 2500);
    } catch {
      // Fallback
    }
  };

  const verifiedCount = properties.filter(p => p.verification_status === "VERIFIED").length;
  const unverifiedCount = properties.length - verifiedCount;

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Inventory Management"
        title="My Properties &amp; Domain Verification"
        description="Register and cryptographically verify website domains and apps where your ad slots are embedded."
        actions={
          <div style={{ display: "flex", gap: "8px" }}>
            <Link to="/publisher/ad-units" className="btn btn-secondary btn-sm">
              Ad Units →
            </Link>
            <Link to="/publisher/integration" className="btn btn-secondary btn-sm">
              Integration Guide →
            </Link>
          </div>
        }
      />

      {error && (
        <Alert variant="danger" title="Error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {formFeedback && (
        <Alert 
          variant={formFeedback.type === "success" ? "success" : "danger"} 
          onClose={() => setFormFeedback(null)}
        >
          {formFeedback.message}
        </Alert>
      )}

      {/* Onboarding Lifecycle Guidance Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "16px", marginBottom: "24px" }}>
        <Card style={{ padding: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)", fontWeight: 600 }}>REGISTERED DOMAINS</div>
            <span style={{ fontSize: "1.25rem", fontWeight: 700 }}>{properties.length}</span>
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", marginTop: "6px" }}>
            Canonical web properties hosting your ad inventory.
          </div>
        </Card>

        <Card style={{ padding: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ fontSize: "0.8125rem", color: "var(--color-success)", fontWeight: 600 }}>VERIFIED PROPERTIES</div>
            <span style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--color-success)" }}>{verifiedCount}</span>
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", marginTop: "6px" }}>
            Active domains authorized for production ad serving.
          </div>
        </Card>

        <Card style={{ padding: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ fontSize: "0.8125rem", color: unverifiedCount > 0 ? "var(--color-warning)" : "var(--color-text-muted)", fontWeight: 600 }}>
              VERIFICATION PENDING
            </div>
            <span style={{ fontSize: "1.25rem", fontWeight: 700, color: unverifiedCount > 0 ? "var(--color-warning)" : "inherit" }}>
              {unverifiedCount}
            </span>
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", marginTop: "6px" }}>
            {unverifiedCount > 0 ? "Requires DNS or HTML meta verification to unlock ads." : "All registered domains are fully verified."}
          </div>
        </Card>

        <Card style={{ padding: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)", fontWeight: 600 }}>TOTAL AD SLOTS</div>
            <span style={{ fontSize: "1.25rem", fontWeight: 700 }}>{slots.length}</span>
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", marginTop: "6px" }}>
            Placement slots bound to your publisher domains.
          </div>
        </Card>
      </div>

      {/* Unverified Property Warning Banner */}
      {unverifiedCount > 0 && (
        <Alert variant="warning" title="Domain Verification Required" style={{ marginBottom: "24px" }}>
          You have {unverifiedCount} unverified domain{unverifiedCount > 1 ? "s" : ""}. To protect network traffic quality and prevent domain spoofing, the ad server automatically suppresses ad serving on unverified properties (returning 403 Forbidden). Click <strong>"Verify Domain"</strong> on any property below to complete DNS TXT or HTML meta verification.
        </Alert>
      )}

      {/* Properties Table Card */}
      <Card
        title="Registered Web &amp; App Properties"
        subtitle="Manage domain verification, ad serving eligibility, and attached placements."
        style={{ marginBottom: "24px" }}
      >
        {loading ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px", padding: "16px 0" }}>
            <LoadingSkeleton variant="rect" height={40} />
            <LoadingSkeleton variant="rect" height={40} />
            <LoadingSkeleton variant="rect" height={40} />
          </div>
        ) : properties.length === 0 ? (
          <EmptyState
            title="No properties registered yet"
            description="Add your first website or app domain below to start configuring verified ad placements."
          />
        ) : (
          <div className="table-container" style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Property &amp; Domain</th>
                  <th>Verification Status</th>
                  <th>Ad Serving</th>
                  <th>Associated Ad Slots</th>
                  <th style={{ textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {properties.map((prop) => {
                  const propSlots = slots.filter((s) => s.property_id === prop.id);
                  const isActive = prop.status === 1;
                  const isVerified = prop.verification_status === "VERIFIED";
                  const isFailed = prop.verification_status === "FAILED";
                  const isPending = prop.verification_status === "PENDING";

                  return (
                    <tr key={prop.id}>
                      <td>
                        <div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: "6px" }}>
                          {prop.name}
                          {prop.is_platform_subdomain && (
                            <span style={{ fontSize: "0.6875rem", background: "var(--color-primary-subtle)", color: "var(--color-primary)", padding: "1px 6px", borderRadius: "10px" }}>
                              Platform Subdomain
                            </span>
                          )}
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "2px" }}>
                          <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.8125rem", color: "var(--color-primary)" }}>
                            {prop.domain}
                          </code>
                          <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>ID #{prop.id}</span>
                        </div>
                      </td>

                      {/* Verification Status Column */}
                      <td>
                        <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                          {isVerified ? (
                            <StatusBadge variant="success" label="Verified" size="sm" />
                          ) : isFailed ? (
                            <StatusBadge variant="error" label="Failed" size="sm" />
                          ) : isPending ? (
                            <StatusBadge variant="warning" label="Pending Check" size="sm" />
                          ) : (
                            <StatusBadge variant="default" label="Unverified" size="sm" />
                          )}
                          <div style={{ fontSize: "0.6875rem", color: "var(--color-text-muted)", marginTop: "2px" }}>
                            {isVerified ? (
                              <span>
                                {prop.verification_method || "DNS_TXT"}
                                {prop.verified_at ? ` • ${new Date(prop.verified_at).toLocaleDateString()}` : ""}
                              </span>
                            ) : prop.last_error ? (
                              <span style={{ color: "var(--color-danger)", display: "block", maxWidth: "220px", textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap" }} title={prop.last_error}>
                                Error: {prop.last_error}
                              </span>
                            ) : isPending ? (
                              <span>Challenge active</span>
                            ) : (
                              <span>Verification required</span>
                            )}
                          </div>
                        </div>
                      </td>

                      {/* Ad Serving Status */}
                      <td>
                        <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                          <StatusBadge 
                            status={isActive ? "active" : "paused"} 
                            label={isActive ? "Active" : "Paused"} 
                            size="sm" 
                          />
                          {!isVerified && isActive && (
                            <span style={{ fontSize: "0.6875rem", color: "var(--color-warning)" }}>
                              (Gated: Needs Verification)
                            </span>
                          )}
                        </div>
                      </td>

                      {/* Associated Ad Slots */}
                      <td>
                        {propSlots.length === 0 ? (
                          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                            <span style={{ color: "var(--color-text-muted)", fontSize: "0.8125rem" }}>0 slots</span>
                            {isVerified && (
                              <Link 
                                to="/publisher/ad-units" 
                                style={{ fontSize: "0.75rem", color: "var(--color-primary)", textDecoration: "underline" }}
                              >
                                + Add Slot
                              </Link>
                            )}
                          </div>
                        ) : (
                          <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                            {propSlots.map((s) => (
                              <span 
                                key={s.id} 
                                style={{ 
                                  fontSize: "0.75rem", 
                                  background: "var(--color-bg-subtle)", 
                                  border: "1px solid var(--border-color)",
                                  padding: "2px 6px", 
                                  borderRadius: "var(--radius-sm)" 
                                }}
                              >
                                {s.name} ({s.width}×{s.height})
                              </span>
                            ))}
                          </div>
                        )}
                      </td>

                      {/* Actions */}
                      <td style={{ textAlign: "right" }}>
                        <div style={{ display: "flex", gap: "6px", justifyContent: "flex-end" }}>
                          <Button
                            variant={isVerified ? "secondary" : "primary"}
                            size="sm"
                            onClick={() => openVerifyModal(prop)}
                          >
                            {isVerified ? "Verification Info" : "Verify Domain"}
                          </Button>
                          <Button
                            variant={isActive ? "secondary" : "primary"}
                            size="sm"
                            onClick={() => setPendingToggle({ id: prop.id, currentStatus: prop.status, name: prop.name })}
                          >
                            {isActive ? "Deactivate" : "Activate"}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Add New Property Card */}
      <Card
        title="Add New Property"
        subtitle="Specify the canonical domain or mobile app identifier for ad inventory verification."
      >
        <form onSubmit={handleCreate}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", marginBottom: "16px" }}>
            <div className="field">
              <label htmlFor="prop-name" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "6px", display: "block" }}>
                Property Name *
              </label>
              <input
                id="prop-name"
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="input"
                placeholder="e.g. Tech Media Portal"
                disabled={submitting}
              />
            </div>
            <div className="field">
              <label htmlFor="prop-domain" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "6px", display: "block" }}>
                Domain Name *
              </label>
              <input
                id="prop-domain"
                type="text"
                required
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
                className="input"
                placeholder="e.g. news.example.com"
                disabled={submitting}
              />
            </div>
          </div>
          <p style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)", marginBottom: "16px" }}>
            Ads will be served to tags embedded under this domain. After registering, you will receive DNS TXT and HTML meta challenge tokens to verify domain ownership.
          </p>
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <Button type="submit" variant="primary" isLoading={submitting} disabled={!name.trim() || !domain.trim()}>
              Add Property
            </Button>
          </div>
        </form>
      </Card>

      {/* Confirmation Dialog for Deactivation / Activation */}
      <ConfirmDialog
        isOpen={!!pendingToggle}
        title={pendingToggle?.currentStatus === 1 ? "Deactivate Property" : "Activate Property"}
        message={
          pendingToggle?.currentStatus === 1
            ? `Are you sure you want to deactivate "${pendingToggle?.name}"? Ad delivery on this domain will be paused immediately.`
            : `Are you sure you want to activate "${pendingToggle?.name}"? Ad delivery will resume on all associated active slots.`
        }
        confirmText={pendingToggle?.currentStatus === 1 ? "Deactivate" : "Activate"}
        variant={pendingToggle?.currentStatus === 1 ? "danger" : "primary"}
        isLoading={toggleLoading}
        onConfirm={confirmToggleStatus}
        onCancel={() => setPendingToggle(null)}
      />

      {/* ── DOMAIN VERIFICATION MODAL ────────────────────────────────────── */}
      {verifyModalProperty && (
        <div 
          className="modal-backdrop" 
          onClick={(e) => {
            if (e.target === e.currentTarget && !isCheckingVerification && !isGeneratingChallenge) {
              closeVerifyModal();
            }
          }}
        >
          <div 
            className="modal-dialog" 
            style={{ maxWidth: "680px", width: "100%" }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="verify-modal-title"
          >
            <div className="modal-header">
              <div>
                <h3 id="verify-modal-title" className="modal-title" style={{ fontSize: "1.125rem", fontWeight: 700 }}>
                  Domain Ownership Verification
                </h3>
                <div style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)", marginTop: "2px" }}>
                  Property: <strong>{verifyModalProperty.name}</strong> (<code style={{ fontFamily: "var(--font-mono)" }}>{verifyModalProperty.domain}</code>)
                </div>
              </div>
              <button
                type="button"
                className="modal-close"
                onClick={closeVerifyModal}
                disabled={isCheckingVerification || isGeneratingChallenge}
                aria-label="Close dialog"
              >
                <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path
                    fillRule="evenodd"
                    d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                    clipRule="evenodd"
                  />
                </svg>
              </button>
            </div>

            <div className="modal-body" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              {/* Verification Feedback Banner */}
              {verificationSuccess && (
                <div style={{ 
                  background: "rgba(16, 185, 129, 0.1)", 
                  border: "1px solid rgba(16, 185, 129, 0.3)", 
                  borderRadius: "var(--radius-md)", 
                  padding: "16px" 
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: 700, color: "var(--color-success)" }}>
                    <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
                      <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                    </svg>
                    Domain Ownership Confirmed!
                  </div>
                  <p style={{ margin: "6px 0 12px", fontSize: "0.875rem", color: "var(--color-text-secondary)" }}>
                    {verifyModalProperty.domain} has been verified and authorized for production ad serving.
                    {verifyModalProperty.verified_at && ` (Verified on ${new Date(verifyModalProperty.verified_at).toLocaleString()})`}
                  </p>
                  <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", borderTop: "1px solid rgba(16, 185, 129, 0.2)", paddingTop: "12px" }}>
                    <Link to="/publisher/ad-units" className="btn btn-primary btn-sm" onClick={closeVerifyModal}>
                      Next: Create Ad Unit →
                    </Link>
                    <Link to="/publisher/integration" className="btn btn-secondary btn-sm" onClick={closeVerifyModal}>
                      Get Embed Snippet →
                    </Link>
                  </div>
                </div>
              )}

              {verificationError && (
                <Alert variant="danger" title="Verification Check Failed">
                  {verificationError}
                </Alert>
              )}

              {/* Instructions intro */}
              {!verificationSuccess && (
                <p style={{ margin: 0, fontSize: "0.875rem", color: "var(--color-text-secondary)" }}>
                  To prevent domain spoofing and authorize ad serving, prove ownership of <strong>{verifyModalProperty.domain}</strong> by adding a DNS TXT record or an HTML meta tag.
                </p>
              )}

              {/* Method Selection Tabs */}
              <div>
                <label style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "8px", display: "block" }}>
                  Select Verification Method:
                </label>
                <div style={{ display: "flex", gap: "12px" }}>
                  <button
                    type="button"
                    onClick={() => setVerificationMethod("DNS_TXT")}
                    className={`btn ${verificationMethod === "DNS_TXT" ? "btn-primary" : "btn-secondary"} btn-sm`}
                    style={{ flex: 1, padding: "8px 12px", textAlign: "center" }}
                  >
                    <strong>DNS TXT Record</strong> (Recommended)
                  </button>
                  <button
                    type="button"
                    onClick={() => setVerificationMethod("HTML_META")}
                    className={`btn ${verificationMethod === "HTML_META" ? "btn-primary" : "btn-secondary"} btn-sm`}
                    style={{ flex: 1, padding: "8px 12px", textAlign: "center" }}
                  >
                    <strong>HTML &lt;meta&gt; Tag</strong> (Fastest)
                  </button>
                </div>
              </div>

              {/* Challenge Generation Action */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", background: "var(--color-bg-subtle)", padding: "12px", borderRadius: "var(--radius-md)" }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: "0.8125rem" }}>
                    {challengeData ? "Active Verification Challenge" : "Ready to Generate Challenge"}
                  </div>
                  <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "2px" }}>
                    {challengeData?.challenge_expires_at ? (
                      `Expires: ${new Date(challengeData.challenge_expires_at).toLocaleString()} (48h validity)`
                    ) : (
                      "Issues an unpredictable cryptographic token valid for 48 hours."
                    )}
                  </div>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleGenerateChallenge}
                  isLoading={isGeneratingChallenge}
                  disabled={isCheckingVerification}
                >
                  {challengeData ? "Renew Challenge" : "Generate Challenge"}
                </Button>
              </div>

              {/* Challenge Details Display */}
              {challengeData && (
                <div style={{ 
                  background: "var(--color-bg-surface)", 
                  border: "1px solid var(--border-color)", 
                  borderRadius: "var(--radius-md)", 
                  padding: "16px" 
                }}>
                  <div style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "12px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <span>Configuration Instructions</span>
                    <span style={{ fontSize: "0.75rem", fontWeight: 400, color: "var(--color-text-muted)" }}>
                      Method: {verificationMethod === "DNS_TXT" ? "DNS TXT" : "HTML Meta"}
                    </span>
                  </div>

                  {verificationMethod === "DNS_TXT" ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                      <p style={{ margin: 0, fontSize: "0.8125rem", color: "var(--color-text-secondary)" }}>
                        Log in to your DNS provider (e.g., Cloudflare, Route53, GoDaddy, Namecheap) and create a TXT record with these exact values:
                      </p>
                      
                      {/* DNS Host Field */}
                      <div>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", fontWeight: 600, color: "var(--color-text-muted)", marginBottom: "4px" }}>
                          <span>RECORD HOST / NAME:</span>
                          <button
                            type="button"
                            onClick={() => copyToClipboard(challengeData.dns_record_name, "dns_name")}
                            style={{ background: "none", border: "none", color: "var(--color-primary)", cursor: "pointer", fontSize: "0.75rem", padding: 0 }}
                          >
                            {copiedField === "dns_name" ? "Copied!" : "Copy Host"}
                          </button>
                        </div>
                        <code style={{ 
                          display: "block", 
                          padding: "8px 12px", 
                          background: "var(--color-bg-subtle)", 
                          borderRadius: "var(--radius-sm)", 
                          fontSize: "0.8125rem", 
                          fontFamily: "var(--font-mono)",
                          border: "1px solid var(--border-color)",
                          wordBreak: "break-all"
                        }}>
                          {challengeData.dns_record_name}
                        </code>
                      </div>

                      {/* DNS Type & Value Field */}
                      <div style={{ display: "grid", gridTemplateColumns: "100px 1fr", gap: "8px" }}>
                        <div>
                          <span style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--color-text-muted)", marginBottom: "4px", display: "block" }}>
                            TYPE:
                          </span>
                          <code style={{ 
                            display: "block", 
                            padding: "8px 12px", 
                            background: "var(--color-bg-subtle)", 
                            borderRadius: "var(--radius-sm)", 
                            fontSize: "0.8125rem", 
                            fontFamily: "var(--font-mono)",
                            border: "1px solid var(--border-color)",
                            textAlign: "center"
                          }}>
                            TXT
                          </code>
                        </div>
                        <div>
                          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", fontWeight: 600, color: "var(--color-text-muted)", marginBottom: "4px" }}>
                            <span>RECORD VALUE:</span>
                            <button
                              type="button"
                              onClick={() => copyToClipboard(challengeData.dns_record_value, "dns_val")}
                              style={{ background: "none", border: "none", color: "var(--color-primary)", cursor: "pointer", fontSize: "0.75rem", padding: 0 }}
                            >
                              {copiedField === "dns_val" ? "Copied!" : "Copy Value"}
                            </button>
                          </div>
                          <code style={{ 
                            display: "block", 
                            padding: "8px 12px", 
                            background: "var(--color-bg-subtle)", 
                            borderRadius: "var(--radius-sm)", 
                            fontSize: "0.8125rem", 
                            fontFamily: "var(--font-mono)",
                            border: "1px solid var(--border-color)",
                            wordBreak: "break-all"
                          }}>
                            {challengeData.dns_record_value}
                          </code>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                      <p style={{ margin: 0, fontSize: "0.8125rem", color: "var(--color-text-secondary)" }}>
                        Paste this &lt;meta&gt; tag inside the <code>&lt;head&gt;</code> element of your website's homepage (<code>https://{verifyModalProperty.domain}/</code>):
                      </p>
                      <div>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", fontWeight: 600, color: "var(--color-text-muted)", marginBottom: "4px" }}>
                          <span>HTML META TAG:</span>
                          <button
                            type="button"
                            onClick={() => copyToClipboard(challengeData.html_meta_tag, "meta_tag")}
                            style={{ background: "none", border: "none", color: "var(--color-primary)", cursor: "pointer", fontSize: "0.75rem", padding: 0 }}
                          >
                            {copiedField === "meta_tag" ? "Copied!" : "Copy Tag"}
                          </button>
                        </div>
                        <pre style={{ 
                          margin: 0, 
                          padding: "10px 12px", 
                          background: "var(--color-bg-subtle)", 
                          borderRadius: "var(--radius-sm)", 
                          fontSize: "0.8125rem", 
                          fontFamily: "var(--font-mono)",
                          border: "1px solid var(--border-color)",
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-all"
                        }}>
                          {challengeData.html_meta_tag}
                        </pre>
                      </div>
                    </div>
                  )}

                  <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "12px" }}>
                    ℹ️ Note: DNS propagation may take anywhere from 1 minute up to 15 minutes depending on your DNS TTL. HTML meta tags are checked immediately upon saving.
                  </div>
                </div>
              )}
            </div>

            <div className="modal-footer" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <Button
                variant="secondary"
                onClick={closeVerifyModal}
                disabled={isCheckingVerification || isGeneratingChallenge}
              >
                Close
              </Button>
              <Button
                variant="primary"
                onClick={handleCheckVerification}
                isLoading={isCheckingVerification}
                disabled={!challengeData && !verifyModalProperty.verification_token}
              >
                Check Verification Now
              </Button>
            </div>
          </div>
        </div>
      )}
    </PageContainer>
  );
}
