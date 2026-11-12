import React, { useState } from "react";
import { BoundSlot, AdPlacementLocation, BlockItem } from "../../types";

export interface AdSlotPaletteProps {
  projectId: number;
  availableSlots: BoundSlot[];
  isLoading: boolean;
  error: string | null;
  onRefresh: () => void;
  onPlaceSlot: (slot: BoundSlot, location: AdPlacementLocation) => void;
  currentBlocks?: BlockItem[];
  onOpenMonetization?: () => void;
}

export const AdSlotPalette: React.FC<AdSlotPaletteProps> = ({
  projectId,
  availableSlots,
  isLoading,
  error,
  onRefresh,
  onPlaceSlot,
  currentBlocks = [],
  onOpenMonetization,
}) => {
  const [filterFormat, setFilterFormat] = useState<"all" | 1 | 2>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [expandedSlotId, setExpandedSlotId] = useState<number | null>(null);

  const filteredSlots = availableSlots.filter((slot) => {
    if (filterFormat !== "all" && slot.format !== filterFormat) {
      return false;
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const matchName = slot.name.toLowerCase().includes(q);
      const matchKey = slot.slot_id.toLowerCase().includes(q);
      if (!matchName && !matchKey) return false;
    }
    return true;
  });

  const getFormatLabel = (format: number): string => {
    switch (format) {
      case 1:
        return "Banner (Format 1)";
      case 2:
        return "Native (Format 2)";
      default:
        return `Format ${format}`;
    }
  };

  const isSlotPlacedOnPage = (slotKey: string): boolean => {
    return currentBlocks.some(
      (b) => b.type === "ad_placement" && b.content?.ad_slot_id === slotKey
    );
  };

  const handleDragStart = (e: React.DragEvent, slot: BoundSlot) => {
    if (slot.status !== 1) {
      e.preventDefault();
      return;
    }
    e.dataTransfer.setData(
      "application/json",
      JSON.stringify({
        type: "ad_slot",
        slot,
      })
    );
    e.dataTransfer.effectAllowed = "copy";
  };

  return (
    <div className="space-y-3" data-testid="ad-slot-palette">
      {/* Header with Title, Count and Refresh */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <span
            style={{
              fontSize: "0.75rem",
              fontWeight: 600,
              color: "var(--text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            Authorized Slots
          </span>
          <span
            style={{
              fontSize: "0.6875rem",
              padding: "0.1rem 0.4rem",
              borderRadius: "9999px",
              backgroundColor: "rgba(37, 99, 235, 0.1)",
              color: "var(--primary)",
              fontWeight: 600,
            }}
            data-testid="ad-slot-count-badge"
          >
            {availableSlots.length}
          </span>
        </div>

        <div style={{ display: "flex", gap: "0.25rem" }}>
          {onOpenMonetization && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={onOpenMonetization}
              style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem" }}
              title="Manage publisher linking, domain verification, and monetization settings"
              data-testid="palette-open-monetization-btn"
            >
              ⚙️ Monetization
            </button>
          )}
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onRefresh}
            disabled={isLoading}
            style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem" }}
            title="Refresh authorized ad slots from server"
            data-testid="ad-slot-palette-refresh"
          >
            {isLoading ? "⟳ Loading..." : "⟳ Refresh"}
          </button>
        </div>
      </div>

      {/* Non-Billable Safeguard Disclosure */}
      <div
        style={{
          padding: "0.625rem 0.75rem",
          background: "rgba(245, 158, 11, 0.08)",
          border: "1px solid rgba(245, 158, 11, 0.25)",
          borderRadius: "var(--radius-sm)",
          fontSize: "0.71875rem",
          color: "var(--text-secondary)",
          lineHeight: 1.35,
        }}
      >
        <strong>Monetization Discovery:</strong> Showing active slots bound to this project's verified publisher property. Placed units remain non-billable preview placeholders in editor.
      </div>

      {/* Filter and Search Bar (when slots exist) */}
      {availableSlots.length > 0 && (
        <div className="space-y-2">
          <input
            type="text"
            className="form-input"
            placeholder="Search slots by name or key..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{ fontSize: "0.75rem", padding: "0.35rem 0.6rem" }}
            data-testid="ad-slot-search-input"
          />

          <div style={{ display: "flex", gap: "0.25rem" }}>
            <button
              type="button"
              className={`btn btn-sm ${filterFormat === "all" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setFilterFormat("all")}
              style={{ flex: 1, fontSize: "0.6875rem", padding: "0.2rem" }}
              data-testid="filter-format-all"
            >
              All ({availableSlots.length})
            </button>
            <button
              type="button"
              className={`btn btn-sm ${filterFormat === 1 ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setFilterFormat(1)}
              style={{ flex: 1, fontSize: "0.6875rem", padding: "0.2rem" }}
              data-testid="filter-format-banner"
            >
              Banner ({availableSlots.filter((s) => s.format === 1).length})
            </button>
            <button
              type="button"
              className={`btn btn-sm ${filterFormat === 2 ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setFilterFormat(2)}
              style={{ flex: 1, fontSize: "0.6875rem", padding: "0.2rem" }}
              data-testid="filter-format-native"
            >
              Native ({availableSlots.filter((s) => s.format === 2).length})
            </button>
          </div>
        </div>
      )}

      {/* Loading State */}
      {isLoading && (
        <div
          style={{
            padding: "2rem 1rem",
            textAlign: "center",
            color: "var(--text-muted)",
            fontSize: "0.8125rem",
          }}
          data-testid="ad-slot-palette-loading"
        >
          <div style={{ marginBottom: "0.5rem", fontSize: "1.25rem" }}>⏳</div>
          Loading authorized publisher slots...
        </div>
      )}

      {/* Error State */}
      {!isLoading && error && (
        <div
          style={{
            padding: "0.875rem",
            backgroundColor: "rgba(239, 68, 68, 0.1)",
            border: "1px solid rgba(239, 68, 68, 0.3)",
            borderRadius: "var(--radius-sm)",
            color: "var(--error)",
            fontSize: "0.75rem",
          }}
          data-testid="ad-slot-palette-error"
        >
          <div style={{ fontWeight: 600, marginBottom: "0.25rem" }}>Failed to Load Slots</div>
          <div style={{ marginBottom: "0.5rem" }}>{error}</div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onRefresh}
            style={{ fontSize: "0.6875rem", padding: "0.2rem 0.5rem" }}
          >
            Retry Connection
          </button>
        </div>
      )}

      {/* Empty State */}
      {!isLoading && !error && availableSlots.length === 0 && (
        <div
          className="card"
          style={{
            padding: "1.5rem 1rem",
            textAlign: "center",
            border: "1px dashed var(--border)",
            backgroundColor: "var(--bg-secondary)",
          }}
          data-testid="ad-slot-palette-empty"
        >
          <div style={{ fontSize: "1.75rem", marginBottom: "0.5rem" }}>📢</div>
          <h4 style={{ fontSize: "0.875rem", fontWeight: 700, margin: "0 0 0.35rem" }}>
            No Authorized Slots Found
          </h4>
          <p
            style={{
              fontSize: "0.75rem",
              color: "var(--text-secondary)",
              lineHeight: 1.4,
              margin: 0,
            }}
          >
            This website project has no active ad slots linked to its publisher property. Link a publisher account with a verified domain to discover eligible ad slots.
          </p>
          {onOpenMonetization && (
            <div style={{ marginTop: "1rem" }}>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={onOpenMonetization}
                data-testid="palette-empty-open-monetization-btn"
                style={{ fontSize: "0.8125rem", padding: "0.35rem 0.75rem" }}
              >
                ⚙️ Configure Publisher Linking
              </button>
            </div>
          )}
        </div>
      )}

      {/* Filtered Empty State */}
      {!isLoading && !error && availableSlots.length > 0 && filteredSlots.length === 0 && (
        <div
          style={{
            padding: "1.5rem 1rem",
            textAlign: "center",
            color: "var(--text-muted)",
            fontSize: "0.75rem",
          }}
        >
          No slots match the current filter or search query.
        </div>
      )}

      {/* Slots List */}
      {!isLoading && !error && filteredSlots.length > 0 && (
        <div className="space-y-2" data-testid="ad-slot-list">
          {filteredSlots.map((slot) => {
            const isPlaced = isSlotPlacedOnPage(slot.slot_id);
            const isActive = slot.status === 1;
            const isExpanded = expandedSlotId === slot.id;

            return (
              <div
                key={slot.id}
                draggable={isActive}
                onDragStart={(e) => handleDragStart(e, slot)}
                style={{
                  padding: "0.75rem",
                  borderRadius: "var(--radius-sm)",
                  border: isPlaced
                    ? "1px solid rgba(16, 185, 129, 0.4)"
                    : "1px solid var(--border)",
                  backgroundColor: "var(--bg-card)",
                  cursor: isActive ? "grab" : "not-allowed",
                  opacity: isActive ? 1 : 0.6,
                  transition: "all 0.15s ease",
                }}
                className="card"
                data-testid={`ad-slot-card-${slot.slot_id}`}
              >
                {/* Top Row: Name, Status, Placed Badge */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "0.5rem" }}>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
                      <span style={{ fontSize: "0.75rem", cursor: "grab" }} title="Drag to canvas">
                        ⋮⋮
                      </span>
                      <strong
                        style={{
                          fontSize: "0.8125rem",
                          color: "var(--text-primary)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                        title={slot.name}
                      >
                        {slot.name}
                      </strong>
                    </div>
                    <div
                      style={{
                        fontSize: "0.6875rem",
                        color: "var(--text-muted)",
                        fontFamily: "monospace",
                        marginTop: "0.15rem",
                      }}
                    >
                      {slot.slot_id}
                    </div>
                  </div>

                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "0.2rem" }}>
                    {isPlaced && (
                      <span
                        style={{
                          fontSize: "0.625rem",
                          padding: "0.1rem 0.35rem",
                          borderRadius: "4px",
                          backgroundColor: "rgba(16, 185, 129, 0.15)",
                          color: "var(--success, #10b981)",
                          fontWeight: 700,
                        }}
                        data-testid={`slot-placed-badge-${slot.slot_id}`}
                      >
                        ✓ Placed
                      </span>
                    )}
                    <span
                      style={{
                        fontSize: "0.625rem",
                        padding: "0.1rem 0.35rem",
                        borderRadius: "4px",
                        backgroundColor: isActive
                          ? "rgba(37, 99, 235, 0.1)"
                          : "rgba(100, 116, 139, 0.1)",
                        color: isActive ? "var(--primary)" : "var(--text-muted)",
                        fontWeight: 600,
                      }}
                    >
                      {isActive ? "Active" : "Inactive"}
                    </span>
                  </div>
                </div>

                {/* Metadata Pills: Format, Dimensions, Floor Price */}
                <div
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: "0.35rem",
                    margin: "0.5rem 0",
                    fontSize: "0.6875rem",
                  }}
                >
                  <span
                    style={{
                      padding: "0.15rem 0.4rem",
                      borderRadius: "4px",
                      backgroundColor:
                        slot.format === 1
                          ? "rgba(59, 130, 246, 0.12)"
                          : "rgba(16, 185, 129, 0.12)",
                      color: slot.format === 1 ? "#2563eb" : "#059669",
                      fontWeight: 600,
                    }}
                  >
                    {getFormatLabel(slot.format)}
                  </span>
                  <span
                    style={{
                      padding: "0.15rem 0.4rem",
                      borderRadius: "4px",
                      backgroundColor: "var(--bg-secondary)",
                      color: "var(--text-secondary)",
                      fontFamily: "monospace",
                    }}
                  >
                    {slot.width}×{slot.height}
                  </span>
                  <span
                    style={{
                      padding: "0.15rem 0.4rem",
                      borderRadius: "4px",
                      backgroundColor: "var(--bg-secondary)",
                      color: "var(--text-secondary)",
                    }}
                  >
                    Floor: ${(slot.floor_price != null ? Number(slot.floor_price) : 0).toFixed(2)}
                  </span>
                  {slot.is_chat_surface && (
                    <span
                      style={{
                        padding: "0.15rem 0.4rem",
                        borderRadius: "4px",
                        backgroundColor: "rgba(139, 92, 246, 0.1)",
                        color: "#7c3aed",
                        fontWeight: 600,
                      }}
                    >
                      Chat Surface
                    </span>
                  )}
                </div>

                {/* Placement Actions */}
                <div style={{ marginTop: "0.5rem" }}>
                  {!isExpanded ? (
                    <div style={{ display: "flex", gap: "0.35rem" }}>
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        disabled={!isActive}
                        onClick={() => onPlaceSlot(slot, "in_article")}
                        style={{ flex: 1, fontSize: "0.6875rem", padding: "0.25rem 0.4rem" }}
                        title="Place into active page content"
                        data-testid={`place-slot-quick-${slot.slot_id}`}
                      >
                        + Place in Content
                      </button>
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        disabled={!isActive}
                        onClick={() => setExpandedSlotId(slot.id)}
                        style={{ fontSize: "0.6875rem", padding: "0.25rem 0.4rem" }}
                        title="Choose placement location"
                        data-testid={`place-slot-options-${slot.slot_id}`}
                      >
                        ▾
                      </button>
                    </div>
                  ) : (
                    <div
                      style={{
                        padding: "0.5rem",
                        backgroundColor: "var(--bg-secondary)",
                        borderRadius: "var(--radius-sm)",
                        border: "1px solid var(--border)",
                      }}
                      className="space-y-1"
                      data-testid={`place-slot-menu-${slot.slot_id}`}
                    >
                      <div
                        style={{
                          fontSize: "0.6875rem",
                          fontWeight: 600,
                          color: "var(--text-secondary)",
                          marginBottom: "0.25rem",
                        }}
                      >
                        Select Placement Location:
                      </div>
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.25rem" }}>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => {
                            onPlaceSlot(slot, "header_banner");
                            setExpandedSlotId(null);
                          }}
                          style={{ fontSize: "0.625rem", padding: "0.25rem" }}
                          data-testid={`place-slot-header-${slot.slot_id}`}
                        >
                          Header Banner
                        </button>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => {
                            onPlaceSlot(slot, "in_article");
                            setExpandedSlotId(null);
                          }}
                          style={{ fontSize: "0.625rem", padding: "0.25rem" }}
                          data-testid={`place-slot-article-${slot.slot_id}`}
                        >
                          In-Article
                        </button>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => {
                            onPlaceSlot(slot, "sidebar");
                            setExpandedSlotId(null);
                          }}
                          style={{ fontSize: "0.625rem", padding: "0.25rem" }}
                          data-testid={`place-slot-sidebar-${slot.slot_id}`}
                        >
                          Sidebar
                        </button>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => {
                            onPlaceSlot(slot, "footer");
                            setExpandedSlotId(null);
                          }}
                          style={{ fontSize: "0.625rem", padding: "0.25rem" }}
                          data-testid={`place-slot-footer-${slot.slot_id}`}
                        >
                          Footer Banner
                        </button>
                      </div>
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => setExpandedSlotId(null)}
                        style={{ width: "100%", fontSize: "0.625rem", padding: "0.15rem", marginTop: "0.25rem" }}
                      >
                        Cancel
                      </button>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

