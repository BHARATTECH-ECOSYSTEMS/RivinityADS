import React, { useEffect, useState } from "react";
import { 
  listPublisherProperties, 
  createPublisherProperty, 
  updatePublisherProperty, 
  type PublisherProperty,
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
      await createPublisherProperty(publisherId, { name: name.trim(), domain: cleanDomain });
      setName("");
      setDomain("");
      setFormFeedback({ type: "success", message: `Property "${cleanDomain}" added successfully.` });
      await loadData();
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

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Inventory Management"
        title="My Properties"
        description="Register and manage website domains and apps where your ad slots are embedded."
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

      {/* Properties Table Card */}
      <Card
        title="Registered Web &amp; App Properties"
        subtitle={`${properties.length} active or pending domain properties.`}
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
            description="Add your first website or app domain below to start configuring ad placements."
          />
        ) : (
          <div className="table-container" style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Property Name</th>
                  <th>Domain</th>
                  <th>Associated Ad Slots</th>
                  <th>Status</th>
                  <th style={{ textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {properties.map((prop) => {
                  const propSlots = slots.filter((s) => s.property_id === prop.id);
                  const isActive = prop.status === 1;

                  return (
                    <tr key={prop.id}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{prop.name}</div>
                        <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>ID #{prop.id}</div>
                      </td>
                      <td>
                        <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.875rem" }}>{prop.domain}</code>
                      </td>
                      <td>
                        {propSlots.length === 0 ? (
                          <span style={{ color: "var(--color-text-muted)", fontSize: "0.8125rem" }}>No slots assigned</span>
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
                      <td>
                        <StatusBadge 
                          status={isActive ? "active" : "paused"} 
                          label={isActive ? "Active" : "Inactive"} 
                          size="sm" 
                        />
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <Button
                          variant={isActive ? "secondary" : "primary"}
                          size="sm"
                          onClick={() => setPendingToggle({ id: prop.id, currentStatus: prop.status, name: prop.name })}
                        >
                          {isActive ? "Deactivate" : "Activate"}
                        </Button>
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
            Ads will be served to tags embedded under this domain. Ensure traffic matches the declared domain to maintain high quality scores.
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
    </PageContainer>
  );
}
