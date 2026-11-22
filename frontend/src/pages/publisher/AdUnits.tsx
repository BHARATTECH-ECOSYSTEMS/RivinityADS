import React, { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  listSlots,
  createSlot,
  updateSlot,
  type SlotData,
  listPublisherProperties,
  type PublisherProperty,
  generateSlotTag
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
import { money } from "../../lib/format";

const FORMAT_NAMES: Record<number, string> = {
  1: "Banner",
  2: "Native",
  3: "Video",
  4: "Interstitial"
};

export default function PublisherAdUnits() {
  const { session } = useAuth();
  const [slots, setSlots] = useState<SlotData[]>([]);
  const [properties, setProperties] = useState<PublisherProperty[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Tag Modal state
  const [showTagModal, setShowTagModal] = useState(false);
  const [currentTag, setCurrentTag] = useState("");
  const [tagSlotName, setTagSlotName] = useState("");
  const [copied, setCopied] = useState(false);
  const [tagLoading, setTagLoading] = useState(false);
  
  // Creation form state
  const [name, setName] = useState("");
  const [slotId, setSlotId] = useState("");
  const [format, setFormat] = useState(1);
  const [width, setWidth] = useState(300);
  const [height, setHeight] = useState(250);
  const [floorPrice, setFloorPrice] = useState(0.0);
  const [propertyId, setPropertyId] = useState<number | "">("");
  const [isChatSurface, setIsChatSurface] = useState(false);
  const [creating, setCreating] = useState(false);
  const [formSuccess, setFormSuccess] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  // Status toggle confirmation
  const [pendingToggle, setPendingToggle] = useState<{ id: number; currentStatus: number; name: string } | null>(null);
  const [toggleLoading, setToggleLoading] = useState(false);

  const location = useLocation();
  const publisherId = session?.user?.publisher_id;

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const propIdParam = params.get("property_id");
    if (propIdParam) {
      setPropertyId(Number(propIdParam));
    }
  }, [location.search]);

  const loadData = async () => {
    if (!publisherId) return;
    try {
      setLoading(true);
      const [slotsData, propsData] = await Promise.all([
        listSlots(publisherId),
        listPublisherProperties(publisherId)
      ]);
      setSlots(slotsData);
      setProperties(propsData);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load ad units");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { 
    void loadData(); 
  }, [publisherId]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setFormError(null);
    setFormSuccess(null);
    try {
      await createSlot({
        slot_id: slotId.trim(),
        name: name.trim(),
        format,
        width: Number(width) || 300,
        height: Number(height) || 250,
        floor_price: Number(floorPrice) || 0,
        property_id: propertyId === "" ? null : Number(propertyId),
        is_chat_surface: isChatSurface,
      });
      setName("");
      setSlotId("");
      setFloorPrice(0);
      setPropertyId("");
      setIsChatSurface(false);
      setFormSuccess(`Ad unit "${name}" created successfully.`);
      await loadData();
    } catch (err: any) {
      setFormError(err.message || "Failed to create ad unit");
    } finally {
      setCreating(false);
    }
  };

  const confirmToggleStatus = async () => {
    if (!pendingToggle) return;
    setToggleLoading(true);
    try {
      const nextStatus = pendingToggle.currentStatus === 1 ? 0 : 1;
      await updateSlot(pendingToggle.id, { status: nextStatus } as any);
      setPendingToggle(null);
      await loadData();
    } catch (err: any) {
      setError(err.message || "Failed to update slot status");
    } finally {
      setToggleLoading(false);
    }
  };

  const handleToggleChatSurface = async (id: number, current: boolean) => {
    try {
      await updateSlot(id, { is_chat_surface: !current } as any);
      await loadData();
    } catch (err: any) {
      setError(err.message || "Failed to update chat surface setting");
    }
  };

  const handleGetTag = async (slot: SlotData) => {
    setTagLoading(true);
    setTagSlotName(slot.name);
    setCopied(false);
    try {
      const res = await generateSlotTag(slot.id);
      setCurrentTag(res.html);
      setShowTagModal(true);
    } catch (err: any) {
      // Fallback standard tag if backend generateSlotTag errors
      const fallbackTag = `<!-- Bharattech Ads Tag: ${slot.name} (${slot.slot_id}) -->
<div id="oas-slot-${slot.slot_id}" class="rivinity-ad-unit" data-slot-id="${slot.slot_id}"></div>
<script async src="/api/v1/sdk.js" data-slot-id="${slot.slot_id}"></script>`;
      setCurrentTag(fallbackTag);
      setShowTagModal(true);
    } finally {
      setTagLoading(false);
    }
  };

  const copyTag = async () => {
    try {
      await navigator.clipboard.writeText(currentTag);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // Clipboard write failed
    }
  };

  const selectedProperty = propertyId !== "" ? properties.find((p) => p.id === Number(propertyId)) : null;
  const unverifiedSlotCount = slots.filter((s) => {
    const prop = properties.find((p) => p.id === s.property_id);
    return prop && prop.verification_status !== "VERIFIED";
  }).length;

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Inventory Management"
        title="Ad Units"
        description="Configure placement slots, set floor prices, generate embed code tags, and optimize fill rates."
        actions={
          <div style={{ display: "flex", gap: "8px" }}>
            <Link to="/publisher/properties" className="btn btn-secondary btn-sm">
              Properties &amp; Verification →
            </Link>
            <Link to="/publisher/integration" className="btn btn-secondary btn-sm">
              Integration Guide →
            </Link>
          </div>
        }
      />

      {unverifiedSlotCount > 0 && (
        <Alert variant="warning" title="Unverified Properties Detected" style={{ marginBottom: "16px" }}>
          {unverifiedSlotCount} ad unit{unverifiedSlotCount > 1 ? "s are" : " is"} bound to unverified domain properties. The ad server automatically suppresses live ad delivery (returning 403 Forbidden) on unverified domains until ownership is verified in <Link to="/publisher/properties" style={{ textDecoration: "underline", fontWeight: 600 }}>Properties</Link>.
        </Alert>
      )}

      {error && (
        <Alert variant="danger" title="Error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {formSuccess && (
        <Alert variant="success" onClose={() => setFormSuccess(null)}>
          {formSuccess}
        </Alert>
      )}

      {formError && (
        <Alert variant="danger" onClose={() => setFormError(null)}>
          {formError}
        </Alert>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "2.4fr 1.6fr", gap: "24px", alignItems: "start" }}>
        
        {/* Ad Units Inventory Table */}
        <Card
          title="Active Placement Slots"
          subtitle={`${slots.length} ad placements configured.`}
        >
          {loading ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px", padding: "16px 0" }}>
              <LoadingSkeleton variant="rect" height={40} />
              <LoadingSkeleton variant="rect" height={40} />
              <LoadingSkeleton variant="rect" height={40} />
            </div>
          ) : slots.length === 0 ? (
            <EmptyState
              title="No ad units created yet"
              description="Configure your first placement slot using the form to generate embed tags."
            />
          ) : (
            <div className="table-container" style={{ overflowX: "auto" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Slot Name &amp; ID</th>
                    <th>Property</th>
                    <th>Format &amp; Size</th>
                    <th>Floor</th>
                    <th>Status</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {slots.map((s) => {
                    const prop = properties.find((p) => p.id === s.property_id);
                    const isActive = s.status === 1;

                    return (
                      <tr key={s.id}>
                        <td>
                          <div style={{ fontWeight: 600 }}>{s.name}</div>
                          <div style={{ fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--color-text-muted)" }}>
                            {s.slot_id}
                          </div>
                        </td>
                        <td>
                          {prop ? (
                            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                              <span style={{ fontSize: "0.8125rem", fontWeight: 600 }}>{prop.name}</span>
                              <span style={{ fontSize: "0.6875rem", color: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}>
                                {prop.domain}
                              </span>
                              {prop.verification_status !== "VERIFIED" ? (
                                <span style={{ fontSize: "0.6875rem", color: "var(--color-warning)", fontWeight: 500 }}>
                                  ⚠️ Unverified Property (Delivery Blocked)
                                </span>
                              ) : (
                                <span style={{ fontSize: "0.6875rem", color: "var(--color-success)", fontWeight: 500 }}>
                                  ✓ Verified
                                </span>
                              )}
                            </div>
                          ) : (
                            <span style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>Unassigned (Global)</span>
                          )}
                        </td>
                        <td>
                          <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                            <span style={{ fontSize: "0.8125rem", fontWeight: 600 }}>
                              {FORMAT_NAMES[s.format] || `Format #${s.format}`}
                            </span>
                            <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                              {s.width}×{s.height}px
                            </span>
                            {s.is_chat_surface && (
                              <span style={{ fontSize: "10px", background: "var(--color-primary-subtle)", color: "var(--color-primary)", padding: "1px 4px", borderRadius: "2px", width: "fit-content" }}>
                                Chat AI
                              </span>
                            )}
                          </div>
                        </td>
                        <td style={{ fontWeight: 600, fontSize: "0.875rem" }}>
                          {money(s.floor_price)}
                        </td>
                        <td>
                          <StatusBadge 
                            status={isActive ? "active" : "paused"} 
                            label={isActive ? "Active" : "Paused"} 
                            size="sm" 
                          />
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <div style={{ display: "flex", gap: "6px", justifyContent: "flex-end" }}>
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => void handleGetTag(s)}
                            >
                              Get Tag
                            </Button>
                            <Button
                              variant={isActive ? "secondary" : "primary"}
                              size="sm"
                              onClick={() => setPendingToggle({ id: s.id, currentStatus: s.status, name: s.name })}
                            >
                              {isActive ? "Pause" : "Activate"}
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

        {/* Create Ad Unit Form */}
        <Card
          title="Create New Ad Unit"
          subtitle="Define placement dimensions and floor price."
        >
          <form onSubmit={handleCreate}>
            <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <div className="field">
                <label htmlFor="slot-name" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                  Unit Name *
                </label>
                <input
                  id="slot-name"
                  type="text"
                  required
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value);
                    if (!slotId) {
                      setSlotId(e.target.value.toLowerCase().replace(/[^a-z0-9]/g, "-"));
                    }
                  }}
                  className="input"
                  placeholder="e.g. Header Leaderboard"
                  disabled={creating}
                />
              </div>

              <div className="field">
                <label htmlFor="slot-id" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                  Unique Slot ID *
                </label>
                <input
                  id="slot-id"
                  type="text"
                  required
                  value={slotId}
                  onChange={(e) => setSlotId(e.target.value)}
                  className="input"
                  placeholder="e.g. header-leaderboard-300x250"
                  disabled={creating}
                />
              </div>

              <div className="field">
                <label htmlFor="property-id" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                  Property
                </label>
                <select
                  id="property-id"
                  value={propertyId}
                  onChange={(e) => setPropertyId(e.target.value ? Number(e.target.value) : "")}
                  className="select"
                  disabled={creating}
                >
                  <option value="">[ None / Global ]</option>
                  {properties.map((p) => {
                    const isVer = p.verification_status === "VERIFIED";
                    return (
                      <option key={p.id} value={p.id}>
                        {p.name} ({p.domain}) [{isVer ? "Verified" : "Unverified"}]
                      </option>
                    );
                  })}
                </select>
                {selectedProperty && selectedProperty.verification_status !== "VERIFIED" && (
                  <div style={{
                    marginTop: "8px",
                    padding: "8px 10px",
                    background: "rgba(245, 158, 11, 0.1)",
                    border: "1px solid rgba(245, 158, 11, 0.3)",
                    borderRadius: "var(--radius-sm)",
                    fontSize: "0.75rem",
                    color: "var(--color-warning)"
                  }}>
                    ⚠️ <strong>Verification Required:</strong> "{selectedProperty.name}" ({selectedProperty.domain}) is unverified. Ad requests will be blocked (403 Forbidden) until domain ownership is verified in <Link to="/publisher/properties" style={{ textDecoration: "underline", fontWeight: 600, color: "var(--color-primary)" }}>Properties</Link>.
                  </div>
                )}
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div className="field">
                  <label htmlFor="format-select" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                    Format
                  </label>
                  <select
                    id="format-select"
                    value={format}
                    onChange={(e) => setFormat(Number(e.target.value))}
                    className="select"
                    disabled={creating}
                  >
                    <option value={1}>Banner</option>
                    <option value={2}>Native</option>
                    <option value={3}>Video</option>
                    <option value={4}>Interstitial</option>
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="floor-price" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                    Floor Price ($)
                  </label>
                  <input
                    id="floor-price"
                    type="number"
                    step="0.01"
                    min="0"
                    value={floorPrice}
                    onChange={(e) => setFloorPrice(Number(e.target.value) || 0)}
                    className="input"
                    disabled={creating}
                  />
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div className="field">
                  <label htmlFor="slot-width" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                    Width (px)
                  </label>
                  <input
                    id="slot-width"
                    type="number"
                    min="1"
                    value={width}
                    onChange={(e) => setWidth(Number(e.target.value) || 300)}
                    className="input"
                    disabled={creating}
                  />
                </div>
                <div className="field">
                  <label htmlFor="slot-height" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                    Height (px)
                  </label>
                  <input
                    id="slot-height"
                    type="number"
                    min="1"
                    value={height}
                    onChange={(e) => setHeight(Number(e.target.value) || 250)}
                    className="input"
                    disabled={creating}
                  />
                </div>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "4px" }}>
                <input
                  id="chat-surface"
                  type="checkbox"
                  checked={isChatSurface}
                  onChange={(e) => setIsChatSurface(e.target.checked)}
                  disabled={creating}
                />
                <label htmlFor="chat-surface" style={{ fontSize: "0.875rem", cursor: "pointer" }}>
                  Chat / AI Surface (ad appears in conversational turns)
                </label>
              </div>

              <div style={{ marginTop: "12px" }}>
                <Button 
                  type="submit" 
                  variant="primary" 
                  isLoading={creating} 
                  disabled={!name.trim() || !slotId.trim()}
                  style={{ width: "100%" }}
                >
                  Create Ad Unit
                </Button>
              </div>
            </div>
          </form>
        </Card>
      </div>

      {/* Integration Code Tag Modal */}
      {showTagModal && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="tag-modal-title"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(15, 23, 42, 0.6)",
            backdropFilter: "blur(2px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: "20px",
          }}
        >
          <div
            style={{
              backgroundColor: "var(--color-bg-surface)",
              borderRadius: "var(--radius-xl)",
              maxWidth: "600px",
              width: "100%",
              padding: "24px",
              boxShadow: "var(--shadow-xl)",
              border: "1px solid var(--border-color)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
              <div>
                <h2 id="tag-modal-title" style={{ margin: 0, fontSize: "1.125rem", fontWeight: 700 }}>
                  Embed Tag: {tagSlotName}
                </h2>
                <p style={{ margin: "4px 0 0", fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                  Paste this snippet directly into your HTML page where the ad should render.
                </p>
              </div>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setShowTagModal(false)}
                aria-label="Close dialog"
                style={{ fontSize: "1.25rem", padding: "4px 8px" }}
              >
                ✕
              </button>
            </div>

            <div
              style={{
                backgroundColor: "var(--color-bg-inverse)",
                color: "#e2e8f0",
                padding: "16px",
                borderRadius: "var(--radius-md)",
                fontFamily: "var(--font-mono)",
                fontSize: "0.8125rem",
                overflowX: "auto",
                whiteSpace: "pre-wrap",
                marginBottom: "20px",
                lineHeight: 1.5,
                border: "1px solid #334155",
              }}
            >
              {currentTag}
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <Button variant="secondary" onClick={() => setShowTagModal(false)}>
                Close
              </Button>
              <Button variant="primary" onClick={copyTag}>
                {copied ? "Copied to Clipboard!" : "Copy Code"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Confirmation Dialog for Status Change */}
      <ConfirmDialog
        isOpen={!!pendingToggle}
        title={pendingToggle?.currentStatus === 1 ? "Pause Ad Unit" : "Activate Ad Unit"}
        message={
          pendingToggle?.currentStatus === 1
            ? `Are you sure you want to pause "${pendingToggle?.name}"? No impressions will be served to this unit while paused.`
            : `Are you sure you want to activate "${pendingToggle?.name}"? The slot will immediately become eligible to receive ads.`
        }
        confirmText={pendingToggle?.currentStatus === 1 ? "Pause Unit" : "Activate Unit"}
        variant={pendingToggle?.currentStatus === 1 ? "danger" : "primary"}
        isLoading={toggleLoading}
        onConfirm={confirmToggleStatus}
        onCancel={() => setPendingToggle(null)}
      />
    </PageContainer>
  );
}
