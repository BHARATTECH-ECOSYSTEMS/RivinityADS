import React, { useState, useEffect } from "react";
import { PropertyVerificationPanel } from "../publisher/PropertyVerificationPanel";
import {
  ProjectData,
  PublisherProperty,
  ProjectMonetizationStatus,
  PropertyChallengeResponse,
  WebsiteMonetizationStatusCode,
  WebsiteMonetizationStatusMap,
  MonetizationConsentScope,
  PropertyVerificationMethod,
} from "../../types";
import {
  listPublisherProperties,
  linkWebsitePublisher,
  challengePublisherProperty,
  verifyPublisherProperty,
  getWebsiteMonetizationStatus,
  recordWebsiteMonetizationConsent,
  enableWebsiteMonetization,
  disableWebsiteMonetization,
  getCurrentUser,
} from "../../api/client";

export interface WebsiteMonetizationModalProps {
  isOpen: boolean;
  onClose: () => void;
  project: ProjectData;
  publisherId?: number | null;
  onProjectUpdated?: (updated: ProjectData) => void;
  onSlotsUpdated?: () => void;
  initialTab?: "linking" | "verification" | "prerequisites" | "consent" | "activation";
  initialMonetizationStatus?: ProjectMonetizationStatus | null;
  initialProperties?: PublisherProperty[];
  initialChallenge?: PropertyChallengeResponse | null;
}

export const WebsiteMonetizationModal: React.FC<WebsiteMonetizationModalProps> = ({
  isOpen,
  onClose,
  project,
  publisherId: propPublisherId,
  onProjectUpdated,
  onSlotsUpdated,
  initialTab = "linking",
  initialMonetizationStatus = null,
  initialProperties = [],
  initialChallenge = null,
}) => {
  const [activeTab, setActiveTab] = useState<
    "linking" | "verification" | "prerequisites" | "consent" | "activation"
  >(initialTab);

  // Core publisher & property state
  const [resolvedPublisherId, setResolvedPublisherId] = useState<number | null>(
    propPublisherId ?? project.publisher_id ?? null
  );
  const [properties, setProperties] = useState<PublisherProperty[]>(initialProperties);
  const [isLoadingProperties, setIsLoadingProperties] = useState(false);
  const [propertiesError, setPropertiesError] = useState<string | null>(null);
  const [isLinking, setIsLinking] = useState(false);
  const [linkSuccessMessage, setLinkSuccessMessage] = useState<string | null>(null);
  const [linkErrorMessage, setLinkErrorMessage] = useState<string | null>(null);

  // Domain verification state
  const [verificationMethod, setVerificationMethod] = useState<"DNS_TXT" | "HTML_META">("DNS_TXT");
  const [challengeData, setChallengeData] = useState<PropertyChallengeResponse | null>(initialChallenge);
  const [isGeneratingChallenge, setIsGeneratingChallenge] = useState(false);
  const [isVerifyingDomain, setIsVerifyingDomain] = useState(false);
  const [verificationFeedback, setVerificationFeedback] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  // Authoritative monetization eligibility state
  const [monetizationStatus, setMonetizationStatus] = useState<ProjectMonetizationStatus | null>(
    initialMonetizationStatus
  );
  const [isLoadingStatus, setIsLoadingStatus] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);

  // Consent form state
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [isRecordingConsent, setIsRecordingConsent] = useState(false);
  const [consentSuccessMessage, setConsentSuccessMessage] = useState<string | null>(null);
  const [consentErrorMessage, setConsentErrorMessage] = useState<string | null>(null);

  // Activation & confirmation state
  const [showEnableConfirm, setShowEnableConfirm] = useState(false);
  const [isEnabling, setIsEnabling] = useState(false);
  const [isDisabling, setIsDisabling] = useState(false);
  const [activationError, setActivationError] = useState<string | null>(null);

  // 1. Initial Load: Resolve publisher and fetch properties & status
  useEffect(() => {
    if (!isOpen) return;

    // Resolve publisher ID if not already passed
    if (!resolvedPublisherId) {
      getCurrentUser()
        .then((user) => {
          if (user?.publisher_id) {
            setResolvedPublisherId(user.publisher_id);
          }
        })
        .catch(() => {
          // Graceful fallback: user may not be signed in as publisher
        });
    }

    loadMonetizationStatus();
  }, [isOpen, project.id]);

  useEffect(() => {
    if (!isOpen || !resolvedPublisherId) return;
    loadProperties(resolvedPublisherId);
  }, [isOpen, resolvedPublisherId]);

  const loadProperties = async (pubId: number) => {
    setIsLoadingProperties(true);
    setPropertiesError(null);
    try {
      const data = await listPublisherProperties(pubId);
      setProperties(data);
    } catch (err: any) {
      setPropertiesError(err?.message || "Failed to load publisher properties");
    } finally {
      setIsLoadingProperties(false);
    }
  };

  const loadMonetizationStatus = async () => {
    setIsLoadingStatus(true);
    setStatusError(null);
    try {
      const data = await getWebsiteMonetizationStatus(project.id);
      setMonetizationStatus(data);
      if (data.consent?.accepted_terms) {
        setTermsAccepted(true);
      }
    } catch (err: any) {
      setStatusError(err?.message || "Failed to load monetization prerequisites");
    } finally {
      setIsLoadingStatus(false);
    }
  };

  if (!isOpen) return null;

  // Currently linked property helper
  const linkedProperty = properties.find((p) => p.id === project.property_id) || null;

  // 2. Publisher Property Linking Action
  const handleLinkProperty = async (property: PublisherProperty) => {
    if (!resolvedPublisherId) return;
    setIsLinking(true);
    setLinkSuccessMessage(null);
    setLinkErrorMessage(null);
    try {
      const updatedProject = await linkWebsitePublisher(project.id, {
        publisher_id: property.publisher_id,
        property_id: property.id,
      });
      setLinkSuccessMessage(`Successfully linked to property "${property.domain}".`);
      if (onProjectUpdated) onProjectUpdated(updatedProject);
      if (onSlotsUpdated) onSlotsUpdated();
      await loadMonetizationStatus();
      await loadProperties(resolvedPublisherId);
    } catch (err: any) {
      setLinkErrorMessage(err?.response?.data?.detail || err?.message || "Failed to link property.");
    } finally {
      setIsLinking(false);
    }
  };

  // 3. Domain Challenge Action
  const handleGenerateChallenge = async (propertyId: number) => {
    setIsGeneratingChallenge(true);
    setVerificationFeedback(null);
    try {
      const challenge = await challengePublisherProperty(propertyId, {
        method: verificationMethod,
      });
      setChallengeData(challenge);
    } catch (err: any) {
      setVerificationFeedback({
        type: "error",
        message: err?.response?.data?.detail || err?.message || "Failed to generate verification challenge.",
      });
    } finally {
      setIsGeneratingChallenge(false);
    }
  };

  // 4. Domain Verify Action
  const handleVerifyDomain = async (propertyId: number) => {
    setIsVerifyingDomain(true);
    setVerificationFeedback(null);
    try {
      const updatedProp = await verifyPublisherProperty(propertyId);
      if (updatedProp.verification_status === "VERIFIED") {
        setVerificationFeedback({
          type: "success",
          message: `Domain ownership verified successfully for ${updatedProp.domain}!`,
        });
      } else {
        setVerificationFeedback({
          type: "error",
          message: `Verification check did not pass (status: ${updatedProp.verification_status}). Please ensure DNS records or meta tags are published.`,
        });
      }
      if (resolvedPublisherId) await loadProperties(resolvedPublisherId);
      await loadMonetizationStatus();
    } catch (err: any) {
      setVerificationFeedback({
        type: "error",
        message: err?.response?.data?.detail || err?.message || "Server domain verification check failed.",
      });
    } finally {
      setIsVerifyingDomain(false);
    }
  };

  // 5. Consent Recording Action
  const handleRecordConsent = async () => {
    if (!termsAccepted) return;
    setIsRecordingConsent(true);
    setConsentSuccessMessage(null);
    setConsentErrorMessage(null);
    try {
      const scope: MonetizationConsentScope = {
        ad_delivery: true,
        revenue_share: "70/30",
        active: true,
      };
      const updatedStatus = await recordWebsiteMonetizationConsent(project.id, {
        accepted_terms: true,
        version: "v1.0",
        scope,
      });
      setMonetizationStatus(updatedStatus);
      setConsentSuccessMessage("Monetization consent and 70/30 revenue-sharing agreement successfully recorded.");
    } catch (err: any) {
      setConsentErrorMessage(err?.response?.data?.detail || err?.message || "Failed to record consent.");
    } finally {
      setIsRecordingConsent(false);
    }
  };

  // 6. Live Monetization Activation & Deactivation Actions
  const handleEnableMonetization = async () => {
    setIsEnabling(true);
    setActivationError(null);
    try {
      const updatedStatus = await enableWebsiteMonetization(project.id);
      setMonetizationStatus(updatedStatus);
      setShowEnableConfirm(false);
      if (onProjectUpdated) {
        onProjectUpdated({
          ...project,
          monetization_status: WebsiteMonetizationStatusCode.ACTIVE_MONETIZED,
        });
      }
    } catch (err: any) {
      setActivationError(err?.response?.data?.detail || err?.message || "Failed to enable live monetization.");
    } finally {
      setIsEnabling(false);
    }
  };

  const handleDisableMonetization = async () => {
    setIsDisabling(true);
    setActivationError(null);
    try {
      const updatedStatus = await disableWebsiteMonetization(project.id);
      setMonetizationStatus(updatedStatus);
      if (onProjectUpdated) {
        onProjectUpdated({
          ...project,
          monetization_status: WebsiteMonetizationStatusCode.DISABLED,
        });
      }
    } catch (err: any) {
      setActivationError(err?.response?.data?.detail || err?.message || "Failed to disable monetization.");
    } finally {
      setIsDisabling(false);
    }
  };

  const currentStatusCode = monetizationStatus?.current_status ?? project.monetization_status ?? 0;
  const currentStatusLabel = WebsiteMonetizationStatusMap[currentStatusCode] || "disabled";

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        backgroundColor: "rgba(0, 0, 0, 0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "1rem",
        backdropFilter: "blur(3px)",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && !isLinking && !isEnabling) {
          onClose();
        }
      }}
      data-testid="monetization-modal"
    >
      <div
        className="card"
        style={{
          width: "100%",
          maxWidth: "820px",
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.25)",
          padding: 0,
          overflow: "hidden",
        }}
      >
        {/* Modal Header */}
        <div
          style={{
            padding: "1.25rem 1.5rem",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            backgroundColor: "var(--bg-card)",
          }}
        >
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <h2 style={{ fontSize: "1.15rem", fontWeight: 700, margin: 0 }}>
                Website Monetization & Publisher Settings
              </h2>
              <span
                style={{
                  fontSize: "0.6875rem",
                  fontWeight: 700,
                  textTransform: "uppercase",
                  padding: "0.15rem 0.5rem",
                  borderRadius: "9999px",
                  backgroundColor:
                    currentStatusCode === 2
                      ? "rgba(16, 185, 129, 0.15)"
                      : "rgba(100, 116, 139, 0.15)",
                  color: currentStatusCode === 2 ? "var(--success, #10b981)" : "var(--text-secondary)",
                }}
                data-testid="monetization-status-badge"
              >
                {currentStatusLabel}
              </span>
            </div>
            <p style={{ margin: "0.2rem 0 0", fontSize: "0.8125rem", color: "var(--text-muted)" }}>
              Link your verified publisher property, review prerequisites, and manage live advertising.
            </p>
          </div>

          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onClose}
            style={{ fontSize: "0.875rem", padding: "0.25rem 0.5rem" }}
            data-testid="monetization-modal-close"
          >
            ✕
          </button>
        </div>

        {/* Modal Navigation Tabs */}
        <div
          style={{
            display: "flex",
            borderBottom: "1px solid var(--border)",
            backgroundColor: "var(--bg-secondary)",
          }}
        >
          <button
            type="button"
            className="btn"
            onClick={() => setActiveTab("linking")}
            style={{
              flex: 1,
              fontSize: "0.75rem",
              padding: "0.625rem 0.5rem",
              borderRadius: 0,
              borderBottom: activeTab === "linking" ? "2px solid var(--primary)" : "none",
              fontWeight: activeTab === "linking" ? 700 : 500,
              color: activeTab === "linking" ? "var(--primary)" : "var(--text-secondary)",
              background: "transparent",
            }}
            data-testid="monetization-tab-linking"
          >
            1. Property Linking
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => setActiveTab("verification")}
            style={{
              flex: 1,
              fontSize: "0.75rem",
              padding: "0.625rem 0.5rem",
              borderRadius: 0,
              borderBottom: activeTab === "verification" ? "2px solid var(--primary)" : "none",
              fontWeight: activeTab === "verification" ? 700 : 500,
              color: activeTab === "verification" ? "var(--primary)" : "var(--text-secondary)",
              background: "transparent",
            }}
            data-testid="monetization-tab-verification"
          >
            2. Domain Verification
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => setActiveTab("prerequisites")}
            style={{
              flex: 1,
              fontSize: "0.75rem",
              padding: "0.625rem 0.5rem",
              borderRadius: 0,
              borderBottom: activeTab === "prerequisites" ? "2px solid var(--primary)" : "none",
              fontWeight: activeTab === "prerequisites" ? 700 : 500,
              color: activeTab === "prerequisites" ? "var(--primary)" : "var(--text-secondary)",
              background: "transparent",
            }}
            data-testid="monetization-tab-prerequisites"
          >
            3. Prerequisites
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => setActiveTab("consent")}
            style={{
              flex: 1,
              fontSize: "0.75rem",
              padding: "0.625rem 0.5rem",
              borderRadius: 0,
              borderBottom: activeTab === "consent" ? "2px solid var(--primary)" : "none",
              fontWeight: activeTab === "consent" ? 700 : 500,
              color: activeTab === "consent" ? "var(--primary)" : "var(--text-secondary)",
              background: "transparent",
            }}
            data-testid="monetization-tab-consent"
          >
            4. Terms & 70/30 Share
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => setActiveTab("activation")}
            style={{
              flex: 1,
              fontSize: "0.75rem",
              padding: "0.625rem 0.5rem",
              borderRadius: 0,
              borderBottom: activeTab === "activation" ? "2px solid var(--primary)" : "none",
              fontWeight: activeTab === "activation" ? 700 : 500,
              color: activeTab === "activation" ? "var(--primary)" : "var(--text-secondary)",
              background: "transparent",
            }}
            data-testid="monetization-tab-activation"
          >
            5. Live Activation
          </button>
        </div>

        {/* Modal Body */}
        <div style={{ padding: "1.5rem", overflowY: "auto", flex: 1 }}>
          {/* ── TAB 1: PROPERTY LINKING ────────────────────────────────────────── */}
          {activeTab === "linking" && (
            <div className="space-y-4" data-testid="linking-tab">
              <div>
                <h3 style={{ fontSize: "1rem", fontWeight: 700, margin: "0 0 0.25rem" }}>
                  Link Website to Publisher Property
                </h3>
                <p style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", margin: 0 }}>
                  Connecting your website project to a publisher property associates your web inventory with active ad slots and earnings reporting.
                </p>
              </div>

              {/* Feedback Banners */}
              {linkSuccessMessage && (
                <div
                  style={{
                    padding: "0.75rem",
                    backgroundColor: "rgba(16, 185, 129, 0.1)",
                    border: "1px solid rgba(16, 185, 129, 0.3)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--success, #10b981)",
                    fontSize: "0.8125rem",
                  }}
                  data-testid="link-success-banner"
                >
                  ✓ {linkSuccessMessage}
                </div>
              )}
              {linkErrorMessage && (
                <div
                  style={{
                    padding: "0.75rem",
                    backgroundColor: "rgba(239, 68, 68, 0.1)",
                    border: "1px solid rgba(239, 68, 68, 0.3)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--error)",
                    fontSize: "0.8125rem",
                  }}
                  data-testid="link-error-banner"
                >
                  ✕ {linkErrorMessage}
                </div>
              )}

              {/* Currently Linked Property */}
              {project.property_id ? (
                <div
                  className="card"
                  style={{
                    padding: "1rem",
                    backgroundColor: "rgba(37, 99, 235, 0.04)",
                    border: "1px solid rgba(37, 99, 235, 0.2)",
                  }}
                  data-testid="linked-property-card"
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div>
                      <span
                        style={{
                          fontSize: "0.6875rem",
                          fontWeight: 700,
                          color: "var(--primary)",
                          textTransform: "uppercase",
                        }}
                      >
                        Currently Linked Property
                      </span>
                      <h4 style={{ fontSize: "1rem", fontWeight: 700, margin: "0.25rem 0 0.1rem" }}>
                        {linkedProperty?.name || `Property #${project.property_id}`}
                      </h4>
                      <code style={{ fontSize: "0.8125rem", color: "var(--text-secondary)" }}>
                        {linkedProperty?.domain || "Domain loading..."}
                      </code>
                    </div>

                    <div style={{ textAlign: "right" }}>
                      <span
                        style={{
                          fontSize: "0.6875rem",
                          padding: "0.15rem 0.45rem",
                          borderRadius: "4px",
                          backgroundColor:
                            linkedProperty?.verification_status === "VERIFIED"
                              ? "rgba(16, 185, 129, 0.15)"
                              : "rgba(245, 158, 11, 0.15)",
                          color:
                            linkedProperty?.verification_status === "VERIFIED"
                              ? "var(--success, #10b981)"
                              : "#92400e",
                          fontWeight: 700,
                        }}
                        data-testid="linked-property-verification-status"
                      >
                        {linkedProperty?.verification_status || "PENDING"}
                      </span>
                    </div>
                  </div>
                </div>
              ) : (
                <div
                  className="card"
                  style={{
                    padding: "1rem",
                    textAlign: "center",
                    border: "1px dashed var(--border)",
                    backgroundColor: "var(--bg-secondary)",
                  }}
                >
                  <p style={{ margin: 0, fontSize: "0.8125rem", color: "var(--text-muted)" }}>
                    No publisher property linked yet. Choose an authorized property below.
                  </p>
                </div>
              )}

              {/* Available Properties Section */}
              <div className="space-y-2">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <label className="form-label" style={{ fontSize: "0.8125rem", margin: 0 }}>
                    Available Publisher Properties {resolvedPublisherId ? `(Publisher #${resolvedPublisherId})` : ""}
                  </label>
                  {resolvedPublisherId && (
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => loadProperties(resolvedPublisherId)}
                      disabled={isLoadingProperties}
                      style={{ fontSize: "0.71875rem", padding: "0.15rem 0.4rem" }}
                    >
                      {isLoadingProperties ? "⟳ Loading..." : "⟳ Refresh"}
                    </button>
                  )}
                </div>

                {!resolvedPublisherId && (
                  <div
                    style={{
                      padding: "0.875rem",
                      backgroundColor: "rgba(245, 158, 11, 0.1)",
                      border: "1px solid rgba(245, 158, 11, 0.3)",
                      borderRadius: "var(--radius-sm)",
                      fontSize: "0.8125rem",
                      color: "var(--text-secondary)",
                    }}
                  >
                    No publisher tenant account detected for your user. Please ensure your account has a registered publisher profile.
                  </div>
                )}

                {isLoadingProperties && (
                  <div style={{ padding: "1.5rem", textAlign: "center", color: "var(--text-muted)", fontSize: "0.8125rem" }}>
                    Loading available publisher properties...
                  </div>
                )}

                {!isLoadingProperties && propertiesError && (
                  <div
                    style={{
                      padding: "0.75rem",
                      backgroundColor: "rgba(239, 68, 68, 0.1)",
                      border: "1px solid rgba(239, 68, 68, 0.3)",
                      borderRadius: "var(--radius-sm)",
                      color: "var(--error)",
                      fontSize: "0.8125rem",
                    }}
                  >
                    {propertiesError}
                  </div>
                )}

                {!isLoadingProperties && !propertiesError && properties.length === 0 && resolvedPublisherId && (
                  <div
                    className="card"
                    style={{
                      padding: "1.5rem",
                      textAlign: "center",
                      border: "1px dashed var(--border)",
                    }}
                    data-testid="no-properties-message"
                  >
                    <p style={{ margin: "0 0 0.5rem", fontSize: "0.8125rem", color: "var(--text-muted)" }}>
                      No properties found under your publisher account.
                    </p>
                    <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                      Register a web domain in the Publisher Inventory portal first, then return here to link it.
                    </span>
                  </div>
                )}

                {!isLoadingProperties && properties.length > 0 && (
                  <div className="space-y-2" data-testid="properties-list">
                    {properties.map((prop) => {
                      const isCurrentlyLinked = prop.id === project.property_id;
                      const isPropActive = prop.status === 1;

                      return (
                        <div
                          key={prop.id}
                          className="card"
                          style={{
                            padding: "0.875rem 1rem",
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                            border: isCurrentlyLinked
                              ? "1px solid var(--primary)"
                              : "1px solid var(--border)",
                          }}
                          data-testid={`property-item-${prop.id}`}
                        >
                          <div>
                            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                              <strong style={{ fontSize: "0.875rem" }}>{prop.name}</strong>
                              <span
                                style={{
                                  fontSize: "0.625rem",
                                  padding: "0.1rem 0.35rem",
                                  borderRadius: "4px",
                                  backgroundColor:
                                    prop.verification_status === "VERIFIED"
                                      ? "rgba(16, 185, 129, 0.15)"
                                      : "rgba(245, 158, 11, 0.15)",
                                  color:
                                    prop.verification_status === "VERIFIED"
                                      ? "var(--success, #10b981)"
                                      : "#92400e",
                                  fontWeight: 600,
                                }}
                              >
                                {prop.verification_status}
                              </span>
                              {!isPropActive && (
                                <span
                                  style={{
                                    fontSize: "0.625rem",
                                    padding: "0.1rem 0.35rem",
                                    borderRadius: "4px",
                                    backgroundColor: "rgba(100, 116, 139, 0.1)",
                                    color: "var(--text-muted)",
                                  }}
                                >
                                  Inactive
                                </span>
                              )}
                            </div>
                            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", fontFamily: "monospace", marginTop: "0.15rem" }}>
                              {prop.domain}
                            </div>
                          </div>

                          <button
                            type="button"
                            className={`btn btn-sm ${isCurrentlyLinked ? "btn-secondary" : "btn-primary"}`}
                            disabled={isCurrentlyLinked || !isPropActive || isLinking}
                            onClick={() => handleLinkProperty(prop)}
                            style={{ fontSize: "0.75rem", padding: "0.25rem 0.6rem" }}
                            data-testid={`link-property-btn-${prop.id}`}
                          >
                            {isCurrentlyLinked ? "Linked" : isLinking ? "Linking..." : "Link to Website"}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ── TAB 2: DOMAIN VERIFICATION ────────────────────────────────────── */}
          {activeTab === "verification" && (
            <div className="space-y-4" data-testid="verification-tab">
              <div>
                <h3 style={{ fontSize: "1rem", fontWeight: 700, margin: "0 0 0.25rem" }}>
                  Domain Ownership Verification
                </h3>
                <p style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", margin: 0 }}>
                  Publishers must prove ownership of website domains before live advertisements can be served. Complete the server-side challenge below.
                </p>
              </div>

              {/* Feedback Alerts */}
              {verificationFeedback && (
                <div
                  style={{
                    padding: "0.75rem",
                    backgroundColor:
                      verificationFeedback.type === "success"
                        ? "rgba(16, 185, 129, 0.1)"
                        : "rgba(239, 68, 68, 0.1)",
                    border: `1px solid ${
                      verificationFeedback.type === "success"
                        ? "rgba(16, 185, 129, 0.3)"
                        : "rgba(239, 68, 68, 0.3)"
                    }`,
                    borderRadius: "var(--radius-sm)",
                    color:
                      verificationFeedback.type === "success"
                        ? "var(--success, #10b981)"
                        : "var(--error)",
                    fontSize: "0.8125rem",
                  }}
                  data-testid="verification-feedback-alert"
                >
                  {verificationFeedback.message}
                </div>
              )}

              {!project.property_id ? (
                <div
                  className="card"
                  style={{ padding: "1.5rem", textAlign: "center", border: "1px dashed var(--border)" }}
                >
                  <p style={{ margin: "0 0 0.5rem", fontSize: "0.875rem", color: "var(--text-muted)" }}>
                    No property is currently linked to this project.
                  </p>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={() => setActiveTab("linking")}
                  >
                    Go to Property Linking
                  </button>
                </div>
              ) : (
                <div className="space-y-4">
                  {/* Property Status Header */}
                  <div
                    className="card"
                    style={{
                      padding: "1rem",
                      backgroundColor: "var(--bg-secondary)",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div>
                        <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>Target Domain:</div>
                        <strong style={{ fontSize: "1rem" }}>
                          {linkedProperty?.domain || `Property #${project.property_id}`}
                        </strong>
                      </div>
                      <span
                        style={{
                          fontSize: "0.75rem",
                          padding: "0.2rem 0.6rem",
                          borderRadius: "4px",
                          backgroundColor:
                            linkedProperty?.verification_status === "VERIFIED"
                              ? "rgba(16, 185, 129, 0.15)"
                              : "rgba(245, 158, 11, 0.15)",
                          color:
                            linkedProperty?.verification_status === "VERIFIED"
                              ? "var(--success, #10b981)"
                              : "#92400e",
                          fontWeight: 700,
                        }}
                        data-testid="domain-verification-status"
                      >
                        {linkedProperty?.verification_status || "PENDING"}
                      </span>
                    </div>
                  </div>

                  {/* Shared Verification Panel */}
                  <div className="card" style={{ padding: "1.25rem" }}>
                    <PropertyVerificationPanel
                      property={{
                        id: project.property_id!,
                        domain: project.custom_domain || `${project.slug}.rivinity.site`,
                        verification_status: linkedProperty?.verification_status || (challengeData ? "PENDING" : "UNVERIFIED"),
                        verification_method: verificationMethod,
                        verification_token: challengeData?.verification_token,
                        challenge_expires_at: challengeData?.challenge_expires_at,
                      }}
                      initialChallenge={challengeData}
                      onChallengeUpdated={(updated) => setChallengeData(updated)}
                      onVerified={async (updatedProp) => {
                        await loadMonetizationStatus();
                        setVerificationFeedback({
                          type: "success",
                          message: `Domain ownership verified successfully for ${updatedProp.domain}!`,
                        });
                      }}
                      showSuccessBanner={false}
                      compact={true}
                      showCreateSlotAction={false}
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ── TAB 3: PREREQUISITES & STATUS ─────────────────────────────────── */}
          {activeTab === "prerequisites" && (
            <div className="space-y-4" data-testid="prerequisites-tab">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <h3 style={{ fontSize: "1rem", fontWeight: 700, margin: "0 0 0.25rem" }}>
                    Authoritative Monetization Prerequisites
                  </h3>
                  <p style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", margin: 0 }}>
                    Every prerequisite below is authoritatively verified by the backend before ad serving can be enabled.
                  </p>
                </div>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={loadMonetizationStatus}
                  disabled={isLoadingStatus}
                  style={{ fontSize: "0.75rem", padding: "0.25rem 0.5rem" }}
                  data-testid="refresh-prereqs-btn"
                >
                  {isLoadingStatus ? "Checking..." : "⟳ Check Status"}
                </button>
              </div>

              {isLoadingStatus && (
                <div style={{ padding: "2rem", textAlign: "center", color: "var(--text-muted)", fontSize: "0.8125rem" }}>
                  Evaluating monetization prerequisites on backend...
                </div>
              )}

              {!isLoadingStatus && monetizationStatus && (
                <div className="space-y-3">
                  {/* Overall Eligibility Banner */}
                  <div
                    style={{
                      padding: "0.875rem",
                      borderRadius: "var(--radius-sm)",
                      backgroundColor: monetizationStatus.is_eligible
                        ? "rgba(16, 185, 129, 0.1)"
                        : "rgba(245, 158, 11, 0.1)",
                      border: `1px solid ${
                        monetizationStatus.is_eligible
                          ? "rgba(16, 185, 129, 0.3)"
                          : "rgba(245, 158, 11, 0.3)"
                      }`,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                    }}
                    data-testid="overall-eligibility-banner"
                  >
                    <div>
                      <strong style={{ fontSize: "0.875rem", color: monetizationStatus.is_eligible ? "var(--success, #10b981)" : "#92400e" }}>
                        {monetizationStatus.is_eligible
                          ? "✓ All Prerequisites Fulfilled"
                          : "⚠️ Action Required — Incomplete Prerequisites"}
                      </strong>
                      <p style={{ margin: "0.2rem 0 0", fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                        {monetizationStatus.is_eligible
                          ? "This project satisfies all backend monetization requirements and is eligible for live activation."
                          : "Resolve the missing items below before monetization can be turned on."}
                      </p>
                    </div>

                    <span style={{ fontSize: "0.75rem", fontWeight: 700, fontFamily: "monospace" }}>
                      Slots: {monetizationStatus.slot_count}
                    </span>
                  </div>

                  {/* Failure Reasons Alert */}
                  {monetizationStatus.failure_reasons.length > 0 && (
                    <div
                      style={{
                        padding: "0.875rem",
                        backgroundColor: "rgba(239, 68, 68, 0.08)",
                        border: "1px solid rgba(239, 68, 68, 0.25)",
                        borderRadius: "var(--radius-sm)",
                        fontSize: "0.8125rem",
                      }}
                      data-testid="prereqs-failure-reasons"
                    >
                      <strong style={{ color: "var(--error)", display: "block", marginBottom: "0.35rem" }}>
                        Reasons Monetization Cannot Be Enabled:
                      </strong>
                      <ul style={{ margin: 0, paddingLeft: "1.25rem", color: "var(--error)" }}>
                        {monetizationStatus.failure_reasons.map((reason, idx) => (
                          <li key={idx} style={{ marginBottom: "0.15rem" }}>
                            {reason}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Prerequisites 6-Point Checklist */}
                  <div className="space-y-2" data-testid="prerequisites-checklist">
                    {[
                      {
                        key: "project_active",
                        title: "1. Website Project Active",
                        description: "Project is in an active draft or published state (not suspended or archived).",
                        fulfilled: monetizationStatus.prerequisites.project_active,
                      },
                      {
                        key: "publisher_active",
                        title: "2. Publisher Account Active",
                        description: "Linked publisher account exists and is in ACTIVE status.",
                        fulfilled: monetizationStatus.prerequisites.publisher_active,
                      },
                      {
                        key: "payout_configured",
                        title: "3. Payout Details Configured",
                        description: "Publisher has configured active bank account or UPI payout details.",
                        fulfilled: monetizationStatus.prerequisites.payout_configured,
                      },
                      {
                        key: "property_verified",
                        title: "4. Publisher Property Verified",
                        description: "Linked web property exists, is ACTIVE, and domain ownership is VERIFIED.",
                        fulfilled: monetizationStatus.prerequisites.property_verified,
                      },
                      {
                        key: "slots_authorized",
                        title: "5. Ad Slots Bound & Authorized",
                        description: "Project contains ad placements bound to active slots owned by this property.",
                        fulfilled: monetizationStatus.prerequisites.slots_authorized,
                      },
                      {
                        key: "consent_recorded",
                        title: "6. Monetization Terms & Consent Recorded",
                        description: "Publisher has explicitly reviewed and accepted terms and 70/30 revenue disclosure.",
                        fulfilled: monetizationStatus.prerequisites.consent_recorded,
                      },
                    ].map((item) => (
                      <div
                        key={item.key}
                        className="card"
                        style={{
                          padding: "0.75rem 1rem",
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          border: item.fulfilled
                            ? "1px solid rgba(16, 185, 129, 0.25)"
                            : "1px solid rgba(239, 68, 68, 0.25)",
                          backgroundColor: item.fulfilled
                            ? "rgba(16, 185, 129, 0.02)"
                            : "rgba(239, 68, 68, 0.02)",
                        }}
                        data-testid={`prereq-item-${item.key}`}
                      >
                        <div>
                          <strong style={{ fontSize: "0.8125rem", color: "var(--text-primary)" }}>
                            {item.title}
                          </strong>
                          <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginTop: "0.15rem" }}>
                            {item.description}
                          </div>
                        </div>

                        <span
                          style={{
                            fontSize: "0.75rem",
                            fontWeight: 700,
                            padding: "0.15rem 0.5rem",
                            borderRadius: "4px",
                            backgroundColor: item.fulfilled
                              ? "rgba(16, 185, 129, 0.15)"
                              : "rgba(239, 68, 68, 0.15)",
                            color: item.fulfilled ? "var(--success, #10b981)" : "var(--error)",
                          }}
                        >
                          {item.fulfilled ? "✓ Fulfilled" : "✕ Incomplete"}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ── TAB 4: TERMS & 70/30 REVENUE SHARING DISCLOSURE ───────────────── */}
          {activeTab === "consent" && (
            <div className="space-y-4" data-testid="consent-tab">
              <div>
                <h3 style={{ fontSize: "1rem", fontWeight: 700, margin: "0 0 0.25rem" }}>
                  Publisher Monetization Terms &amp; Revenue Share
                </h3>
                <p style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", margin: 0 }}>
                  Review the authoritative advertising delivery terms and statutory revenue-sharing model.
                </p>
              </div>

              {/* Feedback Alerts */}
              {consentSuccessMessage && (
                <div
                  style={{
                    padding: "0.75rem",
                    backgroundColor: "rgba(16, 185, 129, 0.1)",
                    border: "1px solid rgba(16, 185, 129, 0.3)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--success, #10b981)",
                    fontSize: "0.8125rem",
                  }}
                  data-testid="consent-success-banner"
                >
                  ✓ {consentSuccessMessage}
                </div>
              )}
              {consentErrorMessage && (
                <div
                  style={{
                    padding: "0.75rem",
                    backgroundColor: "rgba(239, 68, 68, 0.1)",
                    border: "1px solid rgba(239, 68, 68, 0.3)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--error)",
                    fontSize: "0.8125rem",
                  }}
                  data-testid="consent-error-banner"
                >
                  ✕ {consentErrorMessage}
                </div>
              )}

              {/* 70/30 Revenue Share Breakdown Card */}
              <div
                className="card"
                style={{
                  padding: "1.25rem",
                  backgroundColor: "rgba(37, 99, 235, 0.04)",
                  border: "1px solid rgba(37, 99, 235, 0.2)",
                }}
                data-testid="revenue-share-disclosure"
              >
                <div style={{ fontWeight: 700, fontSize: "0.875rem", color: "var(--primary)", marginBottom: "0.5rem" }}>
                  Authoritative Revenue-Sharing Model (70 / 30)
                </div>
                <p style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", lineHeight: 1.45, margin: 0 }}>
                  Under the RivinityADS Publisher Monetization Agreement, the revenue generated by billable impressions and clicks served on your verified properties is divided as follows:
                </p>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: "1rem",
                    margin: "1rem 0",
                  }}
                >
                  <div
                    style={{
                      padding: "1rem",
                      backgroundColor: "var(--bg-card)",
                      borderRadius: "var(--radius-sm)",
                      border: "1px solid var(--border)",
                      textAlign: "center",
                    }}
                  >
                    <div style={{ fontSize: "1.5rem", fontWeight: 800, color: "var(--success, #10b981)" }}>
                      70%
                    </div>
                    <strong style={{ fontSize: "0.8125rem" }}>Publisher Earnings</strong>
                    <div style={{ fontSize: "0.6875rem", color: "var(--text-muted)", marginTop: "0.2rem" }}>
                      Directly credited to publisher ledger balance
                    </div>
                  </div>

                  <div
                    style={{
                      padding: "1rem",
                      backgroundColor: "var(--bg-card)",
                      borderRadius: "var(--radius-sm)",
                      border: "1px solid var(--border)",
                      textAlign: "center",
                    }}
                  >
                    <div style={{ fontSize: "1.5rem", fontWeight: 800, color: "var(--primary)" }}>
                      30%
                    </div>
                    <strong style={{ fontSize: "0.8125rem" }}>Platform Commission</strong>
                    <div style={{ fontSize: "0.6875rem", color: "var(--text-muted)", marginTop: "0.2rem" }}>
                      Covers network infrastructure, fraud filtering & ad delivery
                    </div>
                  </div>
                </div>

                <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", lineHeight: 1.4 }}>
                  Draft previews and unverified traffic produce <strong>zero cost</strong> and are never billable. Live monetization only takes effect after explicit activation and backend validation.
                </div>
              </div>

              {/* Already Recorded Consent Info */}
              {monetizationStatus?.consent && monetizationStatus.consent.accepted_terms && (
                <div
                  className="card"
                  style={{
                    padding: "0.875rem 1rem",
                    backgroundColor: "rgba(16, 185, 129, 0.05)",
                    border: "1px solid rgba(16, 185, 129, 0.25)",
                    fontSize: "0.75rem",
                  }}
                  data-testid="recorded-consent-details"
                >
                  <div style={{ fontWeight: 700, color: "var(--success, #10b981)", marginBottom: "0.25rem" }}>
                    ✓ Current Monetization Consent on Record
                  </div>
                  <div style={{ display: "flex", gap: "1.5rem", color: "var(--text-secondary)" }}>
                    <span>Version: <strong>{monetizationStatus.consent.version}</strong></span>
                    <span>Revenue Share: <strong>{String(monetizationStatus.consent.scope?.revenue_share || "70/30")}</strong></span>
                    <span>Agreed At: <strong>{new Date(monetizationStatus.consent.agreed_at).toLocaleDateString()}</strong></span>
                  </div>
                </div>
              )}

              {/* Consent Recording Form */}
              <div className="card space-y-3" style={{ padding: "1.25rem" }}>
                <label style={{ display: "flex", alignItems: "flex-start", gap: "0.5rem", cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={termsAccepted}
                    onChange={(e) => setTermsAccepted(e.target.checked)}
                    style={{ marginTop: "0.2rem" }}
                    data-testid="consent-checkbox"
                  />
                  <span style={{ fontSize: "0.8125rem", color: "var(--text-primary)", lineHeight: 1.4 }}>
                    I acknowledge and agree to the <strong>RivinityADS Publisher Monetization Terms</strong>, including the <strong>70% publisher / 30% platform revenue share split</strong> and ad delivery disclosures (Version v1.0).
                  </span>
                </label>

                <div style={{ display: "flex", justifyContent: "flex-end" }}>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={!termsAccepted || isRecordingConsent}
                    onClick={handleRecordConsent}
                    data-testid="record-consent-btn"
                  >
                    {isRecordingConsent ? "Recording Consent..." : "Record Monetization Consent"}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* ── TAB 5: LIVE ACTIVATION ────────────────────────────────────────── */}
          {activeTab === "activation" && (
            <div className="space-y-4" data-testid="activation-tab">
              <div>
                <h3 style={{ fontSize: "1rem", fontWeight: 700, margin: "0 0 0.25rem" }}>
                  Monetization Activation &amp; Controls
                </h3>
                <p style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", margin: 0 }}>
                  Control live advertisement delivery for this website project.
                </p>
              </div>

              {activationError && (
                <div
                  style={{
                    padding: "0.75rem",
                    backgroundColor: "rgba(239, 68, 68, 0.1)",
                    border: "1px solid rgba(239, 68, 68, 0.3)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--error)",
                    fontSize: "0.8125rem",
                  }}
                  data-testid="activation-error-banner"
                >
                  ✕ {activationError}
                </div>
              )}

              {/* Status Display Card */}
              <div className="card" style={{ padding: "1.25rem" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>Current Project State:</span>
                    <h4 style={{ fontSize: "1.1rem", fontWeight: 700, margin: "0.2rem 0" }}>
                      {currentStatusLabel.toUpperCase().replace("_", " ")}
                    </h4>
                  </div>
                  <span
                    style={{
                      fontSize: "0.8125rem",
                      fontWeight: 700,
                      padding: "0.25rem 0.75rem",
                      borderRadius: "9999px",
                      backgroundColor:
                        currentStatusCode === 2
                          ? "rgba(16, 185, 129, 0.15)"
                          : "rgba(100, 116, 139, 0.15)",
                      color:
                        currentStatusCode === 2
                          ? "var(--success, #10b981)"
                          : "var(--text-secondary)",
                    }}
                  >
                    Status Code {currentStatusCode}
                  </span>
                </div>

                <div style={{ marginTop: "1rem", fontSize: "0.8125rem", color: "var(--text-secondary)", lineHeight: 1.45 }}>
                  {currentStatusCode === 2 ? (
                    <span>
                      Live monetization is currently <strong>ACTIVE</strong>. Published pages with ad placement blocks will serve real advertisements and record billable events.
                    </span>
                  ) : (
                    <span>
                      Live monetization is currently <strong>DISABLED</strong>. All ad placement blocks display non-billable preview placeholders.
                    </span>
                  )}
                </div>
              </div>

              {/* Activation Trigger Section */}
              {currentStatusCode !== 2 ? (
                <div className="card space-y-3" style={{ padding: "1.25rem" }}>
                  <div style={{ fontWeight: 600, fontSize: "0.875rem" }}>
                    Enable Live Monetization
                  </div>
                  <p style={{ fontSize: "0.8125rem", color: "var(--text-muted)", margin: 0 }}>
                    Enabling monetization activates live ad serving on all published ad units. All 6 prerequisites must be fulfilled.
                  </p>

                  {!monetizationStatus?.is_eligible ? (
                    <div
                      style={{
                        padding: "0.75rem",
                        backgroundColor: "rgba(245, 158, 11, 0.1)",
                        border: "1px solid rgba(245, 158, 11, 0.3)",
                        borderRadius: "var(--radius-sm)",
                        fontSize: "0.75rem",
                        color: "#92400e",
                      }}
                    >
                      Cannot enable monetization: prerequisites are incomplete. Review Tab 3 for details.
                    </div>
                  ) : null}

                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={!monetizationStatus?.is_eligible}
                    onClick={() => setShowEnableConfirm(true)}
                    data-testid="enable-monetization-btn"
                  >
                    Enable Live Monetization...
                  </button>
                </div>
              ) : (
                <div className="card space-y-3" style={{ padding: "1.25rem" }}>
                  <div style={{ fontWeight: 600, fontSize: "0.875rem" }}>
                    Disable Monetization
                  </div>
                  <p style={{ fontSize: "0.8125rem", color: "var(--text-muted)", margin: 0 }}>
                    Immediately halts live advertisement delivery across all project pages, reverting to non-billable placeholders.
                  </p>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={isDisabling}
                    onClick={handleDisableMonetization}
                    data-testid="disable-monetization-btn"
                  >
                    {isDisabling ? "Disabling..." : "Disable Live Monetization"}
                  </button>
                </div>
              )}

              {/* Explicit Confirmation Dialog before Enable */}
              {showEnableConfirm && (
                <div
                  style={{
                    padding: "1.25rem",
                    backgroundColor: "rgba(245, 158, 11, 0.1)",
                    border: "2px solid rgba(245, 158, 11, 0.4)",
                    borderRadius: "var(--radius-md)",
                  }}
                  className="space-y-3"
                  data-testid="enable-confirmation-dialog"
                >
                  <div style={{ fontWeight: 700, fontSize: "0.9375rem", color: "#92400e" }}>
                    ⚠️ Confirm Live Monetization Activation
                  </div>
                  <p style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", lineHeight: 1.45, margin: 0 }}>
                    Are you sure you want to activate live monetization for this website project? Once activated, non-billable preview placeholders on published pages will transition to live ad delivery, and gross revenue will be shared according to the 70/30 split.
                  </p>
                  <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end" }}>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => setShowEnableConfirm(false)}
                      data-testid="cancel-enable-btn"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      disabled={isEnabling}
                      onClick={handleEnableMonetization}
                      data-testid="confirm-enable-btn"
                    >
                      {isEnabling ? "Activating..." : "Yes, Activate Live Monetization"}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
