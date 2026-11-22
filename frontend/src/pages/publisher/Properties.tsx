import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  listPublisherProperties,
  createPublisherProperty,
  updatePublisherProperty,
  type PublisherProperty,
  listSlots,
  type SlotData,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { PropertyVerificationPanel } from "../../components/publisher/PropertyVerificationPanel";
import {
  PageContainer,
  PageHeader,
  Card,
  Button,
  StatusBadge,
  Alert,
  LoadingSkeleton,
  EmptyState,
  ConfirmDialog,
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

  const publisherId = session?.user?.publisher_id;

  const loadData = async () => {
    if (!publisherId) return;
    try {
      setLoading(true);
      const [propsData, slotsData] = await Promise.all([
        listPublisherProperties(publisherId),
        listSlots(publisherId),
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
        message: `Property "${cleanDomain}" added successfully. Please complete domain verification below to enable ad delivery.`,
      });
      await loadData();
      // Automatically open verification modal for newly registered domain
      setVerifyModalProperty(created);
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

  const openVerifyModal = (property: PublisherProperty) => {
    setVerifyModalProperty(property);
  };

  const closeVerifyModal = () => {
    setVerifyModalProperty(null);
    void loadData();
  };

  const handlePropertyVerified = (updated: PublisherProperty) => {
    setProperties((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
    setVerifyModalProperty(updated);
  };

  const verifiedCount = properties.filter((p) => p.verification_status === "VERIFIED").length;
  const unverifiedCount = properties.length - verifiedCount;

  // Lifecycle progression calculator:
  // 1. Property Added -> 2. Verification Pending -> 3. Verified -> 4. Slot Setup -> 5. Integration Tested -> 6. Eligible for Live Ads
  const getLifecycleState = (prop: PublisherProperty, slotCount: number) => {
    const isVerified = prop.verification_status === "VERIFIED";
    const isActive = prop.status === 1;

    if (!isVerified) {
      return {
        stage: 2,
        label: "Stage 2: Verification Pending",
        description: "Add DNS TXT or HTML meta tag to unlock ad delivery.",
        badgeVariant: "warning" as const,
        progressPercent: 33,
      };
    }
    if (slotCount === 0) {
      return {
        stage: 4,
        label: "Stage 4: Slot Setup Needed",
        description: "Domain verified. Create your first ad placement slot.",
        badgeVariant: "default" as const,
        progressPercent: 66,
      };
    }
    if (!isActive) {
      return {
        stage: 6,
        label: "Stage 6: Paused (Eligible)",
        description: "Verified & slots configured. Deactivated by publisher.",
        badgeVariant: "default" as const,
        progressPercent: 100,
      };
    }
    return {
      stage: 6,
      label: "Stage 6: Eligible for Live Ads",
      description: "Fully verified, active, and delivering production ads.",
      badgeVariant: "success" as const,
      progressPercent: 100,
    };
  };

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Inventory Management"
        title="My Properties &amp; Domain Verification"
        description="Register, cryptographically verify, and monitor websites and applications hosting your ad units."
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
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: "16px",
          marginBottom: "24px",
        }}
      >
        <Card style={{ padding: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)", fontWeight: 600 }}>
              REGISTERED DOMAINS
            </div>
            <span style={{ fontSize: "1.25rem", fontWeight: 700 }}>{properties.length}</span>
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", marginTop: "6px" }}>
            Canonical web properties hosting your ad inventory.
          </div>
        </Card>

        <Card style={{ padding: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ fontSize: "0.8125rem", color: "var(--color-success)", fontWeight: 600 }}>
              VERIFIED PROPERTIES
            </div>
            <span style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--color-success)" }}>
              {verifiedCount}
            </span>
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", marginTop: "6px" }}>
            Active domains authorized for production ad serving.
          </div>
        </Card>

        <Card style={{ padding: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div
              style={{
                fontSize: "0.8125rem",
                color: unverifiedCount > 0 ? "var(--color-warning)" : "var(--color-text-muted)",
                fontWeight: 600,
              }}
            >
              VERIFICATION PENDING
            </div>
            <span
              style={{
                fontSize: "1.25rem",
                fontWeight: 700,
                color: unverifiedCount > 0 ? "var(--color-warning)" : "inherit",
              }}
            >
              {unverifiedCount}
            </span>
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", marginTop: "6px" }}>
            {unverifiedCount > 0
              ? "Requires DNS or HTML meta verification to unlock ads."
              : "All registered domains are fully verified."}
          </div>
        </Card>

        <Card style={{ padding: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)", fontWeight: 600 }}>
              TOTAL AD SLOTS
            </div>
            <span style={{ fontSize: "1.25rem", fontWeight: 700 }}>{slots.length}</span>
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", marginTop: "6px" }}>
            Placement slots bound to your publisher domains.
          </div>
        </Card>
      </div>

      {/* Lifecycle Stepper Info Card */}
      <Card style={{ marginBottom: "24px", padding: "16px" }}>
        <div style={{ fontSize: "0.8125rem", fontWeight: 700, color: "var(--color-text-primary)", marginBottom: "8px" }}>
          PUBLISHER ONBOARDING LIFECYCLE
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px", fontSize: "0.75rem" }}>
          <span style={{ padding: "4px 8px", background: "var(--color-bg-subtle)", borderRadius: "4px", fontWeight: 600 }}>
            1. Property Added
          </span>
          <span>→</span>
          <span style={{ padding: "4px 8px", background: "var(--color-bg-subtle)", borderRadius: "4px", fontWeight: 600 }}>
            2. Verification Pending
          </span>
          <span>→</span>
          <span style={{ padding: "4px 8px", background: "var(--color-bg-subtle)", borderRadius: "4px", fontWeight: 600 }}>
            3. Verified
          </span>
          <span>→</span>
          <span style={{ padding: "4px 8px", background: "var(--color-bg-subtle)", borderRadius: "4px", fontWeight: 600 }}>
            4. Slot Setup
          </span>
          <span>→</span>
          <span style={{ padding: "4px 8px", background: "var(--color-bg-subtle)", borderRadius: "4px", fontWeight: 600 }}>
            5. Integration Tested
          </span>
          <span>→</span>
          <span style={{ padding: "4px 8px", background: "var(--color-primary-subtle)", color: "var(--color-primary)", borderRadius: "4px", fontWeight: 700 }}>
            6. Eligible for Live Ads
          </span>
        </div>
      </Card>

      {/* Unverified Property Warning Banner */}
      {unverifiedCount > 0 && (
        <Alert variant="warning" title="Domain Verification Required" style={{ marginBottom: "24px" }}>
          You have {unverifiedCount} unverified domain{unverifiedCount > 1 ? "s" : ""}. To protect network traffic quality
          and prevent domain spoofing, the ad server automatically suppresses ad serving on unverified properties
          (returning 403 Forbidden). Click <strong>"Verify Domain"</strong> on any property below to complete DNS TXT or
          HTML meta verification.
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
                  <th>Lifecycle Stage</th>
                  <th>Verification Status</th>
                  <th>Ad Serving</th>
                  <th>Associated Ad Slots</th>
                  <th style={{ textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {properties.map((prop) => {
                  const propSlots = slots.filter((s) => s.property_id === prop.id);
                  const effectiveSlotCount = prop.slot_count ?? propSlots.length;
                  const isActive = prop.status === 1;
                  const isVerified = prop.verification_status === "VERIFIED";
                  const isFailed = prop.verification_status === "FAILED";
                  const isPending = prop.verification_status === "PENDING";
                  const lifecycle = getLifecycleState(prop, effectiveSlotCount);

                  return (
                    <tr key={prop.id}>
                      {/* Property & Domain */}
                      <td>
                        <div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: "6px" }}>
                          {prop.name}
                          {prop.is_platform_subdomain && (
                            <span
                              style={{
                                fontSize: "0.6875rem",
                                background: "var(--color-primary-subtle)",
                                color: "var(--color-primary)",
                                padding: "1px 6px",
                                borderRadius: "10px",
                              }}
                            >
                              Platform Subdomain
                            </span>
                          )}
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "2px" }}>
                          <code
                            style={{
                              fontFamily: "var(--font-mono)",
                              fontSize: "0.8125rem",
                              color: "var(--color-primary)",
                            }}
                          >
                            {prop.domain}
                          </code>
                          <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>ID #{prop.id}</span>
                        </div>
                        {/* Health Warnings Display */}
                        {prop.health_warnings && prop.health_warnings.length > 0 && (
                          <div style={{ marginTop: "4px", display: "flex", flexDirection: "column", gap: "2px" }}>
                            {prop.health_warnings.map((w, idx) => (
                              <span
                                key={idx}
                                style={{
                                  fontSize: "0.6875rem",
                                  color: "var(--color-warning)",
                                  display: "flex",
                                  alignItems: "center",
                                  gap: "4px",
                                }}
                              >
                                <span>⚠️</span> {w}
                              </span>
                            ))}
                          </div>
                        )}
                      </td>

                      {/* Lifecycle Progression Column */}
                      <td>
                        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                          <StatusBadge variant={lifecycle.badgeVariant} label={lifecycle.label} size="sm" />
                          <span style={{ fontSize: "0.6875rem", color: "var(--color-text-muted)" }}>
                            {lifecycle.description}
                          </span>
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
                              <span
                                style={{
                                  color: "var(--color-danger)",
                                  display: "block",
                                  maxWidth: "200px",
                                  textOverflow: "ellipsis",
                                  overflow: "hidden",
                                  whiteSpace: "nowrap",
                                }}
                                title={prop.last_error}
                              >
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

                      {/* Ad Serving Status (Distinct from verification) */}
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
                        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                            <span style={{ color: "var(--color-text-muted)", fontSize: "0.8125rem", fontWeight: 600 }}>
                              {effectiveSlotCount} slot{effectiveSlotCount === 1 ? "" : "s"}
                            </span>
                            {isVerified && (
                              <Link
                                to={`/publisher/ad-units?property_id=${prop.id}`}
                                style={{
                                  fontSize: "0.75rem",
                                  color: "var(--color-primary)",
                                  textDecoration: "underline",
                                  fontWeight: 600,
                                }}
                              >
                                + Add Slot
                              </Link>
                            )}
                          </div>
                          {propSlots.length > 0 && (
                            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                              {propSlots.slice(0, 3).map((s) => (
                                <span
                                  key={s.id}
                                  style={{
                                    fontSize: "0.75rem",
                                    background: "var(--color-bg-subtle)",
                                    border: "1px solid var(--border-color)",
                                    padding: "2px 6px",
                                    borderRadius: "var(--radius-sm)",
                                  }}
                                >
                                  {s.name} ({s.width}×{s.height})
                                </span>
                              ))}
                              {propSlots.length > 3 && (
                                <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                                  +{propSlots.length - 3} more
                                </span>
                              )}
                            </div>
                          )}
                        </div>
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
                            onClick={() =>
                              setPendingToggle({ id: prop.id, currentStatus: prop.status, name: prop.name })
                            }
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
              <label
                htmlFor="prop-name"
                style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "6px", display: "block" }}
              >
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
              <label
                htmlFor="prop-domain"
                style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "6px", display: "block" }}
              >
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
            Ads will be served to tags embedded under this domain. After registering, you will receive DNS TXT and HTML
            meta challenge tokens to verify domain ownership.
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

      {/* ── SHARED DOMAIN VERIFICATION MODAL ────────────────────────────────────── */}
      {verifyModalProperty && (
        <div
          className="modal-backdrop"
          onClick={(e) => {
            if (e.target === e.currentTarget) {
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
                  Property: <strong>{verifyModalProperty.name}</strong> (
                  <code style={{ fontFamily: "var(--font-mono)" }}>{verifyModalProperty.domain}</code>)
                </div>
              </div>
              <button
                type="button"
                className="modal-close"
                onClick={closeVerifyModal}
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

            <div className="modal-body">
              <PropertyVerificationPanel
                property={verifyModalProperty}
                onVerified={handlePropertyVerified}
                onClose={closeVerifyModal}
                showSuccessBanner={true}
                showCreateSlotAction={true}
              />
            </div>

            <div className="modal-footer" style={{ display: "flex", justifyContent: "flex-end" }}>
              <Button variant="secondary" onClick={closeVerifyModal}>
                Close
              </Button>
            </div>
          </div>
        </div>
      )}
    </PageContainer>
  );
}
