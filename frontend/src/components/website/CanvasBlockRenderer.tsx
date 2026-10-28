import React from "react";
import type { BlockItem, ThemeConfig } from "../../types";

interface CanvasBlockRendererProps {
  block: BlockItem;
  theme: ThemeConfig;
  isSelected: boolean;
  onSelect: () => void;
  onMoveUp?: () => void;
  onMoveDown?: () => void;
  onDelete: () => void;
  onToggleVisibility?: () => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
}

export const CanvasBlockRenderer: React.FC<CanvasBlockRendererProps> = ({
  block,
  theme,
  isSelected,
  onSelect,
  onMoveUp,
  onMoveDown,
  onDelete,
  onToggleVisibility,
  canMoveUp,
  canMoveDown,
}) => {
  const content = block.content || {};

  const blockTypeLabel = {
    hero: "Hero Section",
    text_section: "Text Section",
    features_grid: "Features Grid",
    image_gallery: "Image Gallery",
    contact_form: "Contact Form",
    cta_banner: "Call-to-Action Banner",
    ad_placement: "Ad Placement Placeholder",
    footer: "Footer Section",
  }[block.type] || block.type;

  return (
    <div
      className={`wb-canvas-block ${isSelected ? "is-selected" : ""} ${!block.is_visible ? "is-hidden-block" : ""}`}
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
      tabIndex={0}
      role="button"
      aria-label={`Edit ${blockTypeLabel}`}
      style={{
        opacity: block.is_visible ? 1 : 0.5,
        borderBottom: "1px solid #f1f5f9",
      }}
    >
      {/* Action pill on hover/selection */}
      <div className="wb-canvas-block-header-pill" onClick={(e) => e.stopPropagation()}>
        <span style={{ marginRight: 4, fontWeight: 700 }}>{blockTypeLabel}</span>
        <button
          type="button"
          onClick={onToggleVisibility}
          title={block.is_visible ? "Hide block" : "Show block"}
          aria-label={block.is_visible ? "Hide block" : "Show block"}
        >
          {block.is_visible ? "👁️" : "🙈"}
        </button>
        <button
          type="button"
          onClick={onMoveUp}
          disabled={!canMoveUp}
          title="Move up"
          aria-label="Move up"
        >
          ▲
        </button>
        <button
          type="button"
          onClick={onMoveDown}
          disabled={!canMoveDown}
          title="Move down"
          aria-label="Move down"
        >
          ▼
        </button>
        <button
          type="button"
          onClick={onDelete}
          title="Delete block"
          aria-label="Delete block"
          style={{ color: "#f87171" }}
        >
          ✕
        </button>
      </div>

      {/* Render block visually according to type */}
      {block.type === "hero" && (
        <section
          style={{
            padding: "4rem 2rem",
            textAlign: (content.alignment || "left") as any,
            backgroundColor: content.background_image_url ? "#1e293b" : theme.background_color,
            color: content.background_image_url ? "#ffffff" : theme.text_color,
            backgroundImage: content.background_image_url
              ? `linear-gradient(rgba(0,0,0,0.5), rgba(0,0,0,0.5)), url('${content.background_image_url}')`
              : "none",
            backgroundSize: "cover",
            backgroundPosition: "center",
          }}
        >
          <div style={{ maxWidth: 800, margin: content.alignment === "center" ? "0 auto" : content.alignment === "right" ? "0 0 0 auto" : "0" }}>
            <h1 style={{ fontSize: "2.5rem", fontWeight: 800, marginBottom: "1rem", lineHeight: 1.2 }}>
              {content.headline || "Welcome to Your Website"}
            </h1>
            {content.subheadline && (
              <p style={{ fontSize: "1.2rem", opacity: 0.9, marginBottom: "1.75rem", lineHeight: 1.5 }}>
                {content.subheadline}
              </p>
            )}
            {content.cta_button && (
              <span
                style={{
                  display: "inline-block",
                  padding: "0.75rem 1.75rem",
                  backgroundColor: theme.primary_color,
                  color: "#ffffff",
                  borderRadius: theme.border_radius,
                  fontWeight: 600,
                  fontSize: "1rem",
                }}
              >
                {content.cta_button.label || "Get Started"}
              </span>
            )}
          </div>
        </section>
      )}

      {block.type === "text_section" && (
        <section style={{ padding: "3rem 2rem", backgroundColor: theme.background_color, color: theme.text_color }}>
          <div style={{ maxWidth: 840, margin: "0 auto" }}>
            {content.heading && (
              <h2 style={{ fontSize: "1.875rem", fontWeight: 700, marginBottom: "1rem" }}>
                {content.heading}
              </h2>
            )}
            <div style={{ fontSize: "1.05rem", lineHeight: 1.7, opacity: 0.85 }}>
              {(content.body || "Add your body text, story, or article here.")
                .split("\n\n")
                .map((paragraph: string, idx: number) => (
                  <p key={idx} style={{ marginBottom: "1rem" }}>{paragraph}</p>
                ))}
            </div>
          </div>
        </section>
      )}

      {block.type === "features_grid" && (
        <section style={{ padding: "3.5rem 2rem", backgroundColor: "rgba(0,0,0,0.02)", color: theme.text_color }}>
          <div style={{ maxWidth: 1000, margin: "0 auto" }}>
            {content.title && (
              <h2 style={{ fontSize: "2rem", fontWeight: 700, textAlign: "center", marginBottom: "2.5rem" }}>
                {content.title}
              </h2>
            )}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "1.5rem" }}>
              {(content.items || []).map((item: any, idx: number) => (
                <div
                  key={idx}
                  style={{
                    padding: "1.75rem",
                    backgroundColor: "#ffffff",
                    border: "1px solid #e2e8f0",
                    borderRadius: theme.border_radius,
                    boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
                  }}
                >
                  <div
                    style={{
                      width: 40,
                      height: 40,
                      borderRadius: theme.border_radius,
                      backgroundColor: "rgba(37, 99, 235, 0.1)",
                      color: theme.primary_color,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 18,
                      fontWeight: 700,
                      marginBottom: "1rem",
                    }}
                  >
                    ★
                  </div>
                  <h3 style={{ fontSize: "1.15rem", fontWeight: 600, marginBottom: "0.5rem" }}>
                    {item.title || "Feature Title"}
                  </h3>
                  <p style={{ fontSize: "0.925rem", color: "#64748b", lineHeight: 1.5 }}>
                    {item.description || "Description of this capability or service."}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {block.type === "image_gallery" && (
        <section style={{ padding: "3.5rem 2rem", backgroundColor: theme.background_color }}>
          <div style={{ maxWidth: 1000, margin: "0 auto" }}>
            {content.title && (
              <h2 style={{ fontSize: "2rem", fontWeight: 700, textAlign: "center", marginBottom: "2rem" }}>
                {content.title}
              </h2>
            )}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: "1.25rem" }}>
              {(content.images || []).map((img: any, idx: number) => (
                <div
                  key={idx}
                  style={{
                    overflow: "hidden",
                    borderRadius: theme.border_radius,
                    border: "1px solid #e2e8f0",
                    backgroundColor: "#ffffff",
                  }}
                >
                  <div style={{ height: 160, backgroundColor: "#f1f5f9", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
                    {img.image_url ? (
                      <img src={img.image_url} alt={img.alt_text || "Photo"} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                    ) : (
                      <span style={{ color: "#94a3b8" }}>🖼️ Image Photo</span>
                    )}
                  </div>
                  {img.caption && (
                    <div style={{ padding: "0.5rem 0.75rem", fontSize: "0.85rem", color: "#64748b" }}>
                      {img.caption}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {block.type === "contact_form" && (
        <section style={{ padding: "3.5rem 2rem", backgroundColor: "rgba(0,0,0,0.01)" }}>
          <div
            style={{
              maxWidth: 540,
              margin: "0 auto",
              padding: "2rem",
              backgroundColor: "#ffffff",
              border: "1px solid #e2e8f0",
              borderRadius: theme.border_radius,
              boxShadow: "0 2px 4px rgba(0,0,0,0.05)",
            }}
          >
            <h2 style={{ fontSize: "1.5rem", fontWeight: 700, textAlign: "center", marginBottom: "1.5rem" }}>
              {content.title || "Contact Us"}
            </h2>
            <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
              {(content.fields || ["name", "email", "message"]).map((f: string) => (
                <div key={f} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <label style={{ fontSize: "0.85rem", fontWeight: 500, color: "#475569", textTransform: "capitalize" }}>
                    {f}
                  </label>
                  <div
                    style={{
                      height: f === "message" ? 70 : 36,
                      border: "1px solid #cbd5e1",
                      borderRadius: theme.border_radius,
                      backgroundColor: "#f8fafc",
                    }}
                  />
                </div>
              ))}
              <div
                style={{
                  padding: "0.75rem",
                  backgroundColor: theme.primary_color,
                  color: "#ffffff",
                  textAlign: "center",
                  borderRadius: theme.border_radius,
                  fontWeight: 600,
                  fontSize: "0.95rem",
                  marginTop: "0.5rem",
                }}
              >
                {content.submit_label || "Send Message"}
              </div>
            </div>
          </div>
        </section>
      )}

      {block.type === "cta_banner" && (
        <section
          style={{
            padding: "3.5rem 2rem",
            backgroundColor: theme.primary_color,
            color: "#ffffff",
            textAlign: "center",
          }}
        >
          <div style={{ maxWidth: 680, margin: "0 auto" }}>
            <h2 style={{ fontSize: "2rem", fontWeight: 800, marginBottom: "0.75rem" }}>
              {content.title || "Ready to take the next step?"}
            </h2>
            {content.description && (
              <p style={{ fontSize: "1.1rem", opacity: 0.9, marginBottom: "1.5rem" }}>
                {content.description}
              </p>
            )}
            {content.button && (
              <span
                style={{
                  display: "inline-block",
                  padding: "0.75rem 1.75rem",
                  backgroundColor: "#ffffff",
                  color: theme.primary_color,
                  borderRadius: theme.border_radius,
                  fontWeight: 700,
                  fontSize: "1rem",
                }}
              >
                {content.button.label || "Learn More"}
              </span>
            )}
          </div>
        </section>
      )}

      {block.type === "ad_placement" && (
        <div style={{ padding: "2rem", display: "flex", justifyContent: "center" }}>
          <div
            style={{
              width: "100%",
              maxWidth: 728,
              minHeight: 90,
              border: "2px dashed #94a3b8",
              borderRadius: theme.border_radius,
              backgroundColor: "#f8fafc",
              padding: "1.25rem",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              textAlign: "center",
            }}
          >
            <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.5rem" }}>
              <span style={{ background: "#e2e8f0", color: "#475569", fontSize: "0.7rem", fontWeight: 700, textTransform: "uppercase", padding: "2px 8px", borderRadius: 4 }}>
                Advertisement
              </span>
              <span style={{ background: "#fef3c7", color: "#92400e", fontSize: "0.7rem", fontWeight: 700, textTransform: "uppercase", padding: "2px 8px", borderRadius: 4 }}>
                Non-Billable Preview Placeholder
              </span>
            </div>
            <p style={{ fontWeight: 600, fontSize: "0.9rem", color: "#334155" }}>
              RivinityADS Placement Slot ({content.placement_location || "in_article"})
            </p>
            <p style={{ fontSize: "0.75rem", color: "#64748b", marginTop: 4 }}>
              Format: {content.format === 1 ? "Standard Display Banner" : "Native Card"} • Live monetization inactive • Strictly disabled during draft preview
            </p>
            {content.ad_slot_id && (
              <span style={{ fontSize: "0.7rem", color: "#94a3b8", marginTop: 2, fontFamily: "monospace" }}>
                Slot: {content.ad_slot_id}
              </span>
            )}
          </div>
        </div>
      )}

      {block.type === "footer" && (
        <footer
          style={{
            padding: "2.5rem 2rem",
            backgroundColor: "#0f172a",
            color: "#94a3b8",
            textAlign: "center",
          }}
        >
          <div style={{ maxWidth: 800, margin: "0 auto", display: "flex", flexDirection: "column", gap: "1rem" }}>
            {content.links && content.links.length > 0 && (
              <div style={{ display: "flex", gap: "1.5rem", justifyContent: "center", flexWrap: "wrap" }}>
                {content.links.map((link: any, idx: number) => (
                  <span key={idx} style={{ color: "#cbd5e1", fontSize: "0.9rem" }}>
                    {link.label || "Link"}
                  </span>
                ))}
              </div>
            )}
            <p style={{ fontSize: "0.85rem", color: "#64748b" }}>
              {content.copyright_text || "© 2026 Powered by RivinityADS"}
            </p>
          </div>
        </footer>
      )}
    </div>
  );
};
