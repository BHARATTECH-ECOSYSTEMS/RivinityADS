import React, { useState } from "react";
import { BlockItem, BlockType } from "../../types";

interface BlockPropertyFormProps {
  block: BlockItem;
  onChange: (updated: BlockItem) => void;
  onDelete: (blockId: string) => void;
  onClose?: () => void;
}

export const BlockPropertyForm: React.FC<BlockPropertyFormProps> = ({
  block,
  onChange,
  onDelete,
  onClose,
}) => {
  const [confirmDelete, setConfirmDelete] = useState(false);

  const updateContent = (partial: Record<string, any>) => {
    onChange({
      ...block,
      content: {
        ...block.content,
        ...partial,
      },
    });
  };

  const toggleVisibility = () => {
    onChange({
      ...block,
      is_visible: !block.is_visible,
    });
  };

  // Helper validation for safe URLs
  const validateUrl = (url: string): string | null => {
    if (!url) return null;
    const lower = url.trim().toLowerCase();
    if (
      lower.includes("<script") ||
      lower.includes("javascript:") ||
      lower.includes("data:text/html") ||
      lower.includes("<iframe")
    ) {
      return "Executable scripts or dangerous schemes are forbidden";
    }
    if (!/^(https?:\/\/|\/|mailto:|tel:)/i.test(lower)) {
      return "URL must start with http://, https://, /, mailto:, or tel:";
    }
    return null;
  };

  return (
    <div className="wb-block-inspector" data-testid="block-property-form">
      {/* Inspector Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <div>
          <span className="wb-badge" style={{ textTransform: "uppercase", fontSize: "0.6875rem" }}>
            {block.type.replace("_", " ")}
          </span>
          <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.25rem", fontFamily: "monospace" }}>
            ID: {block.id}
          </div>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={toggleVisibility}
            title={block.is_visible ? "Hide block from published site" : "Show block"}
            style={{ fontSize: "0.75rem", padding: "0.25rem 0.5rem" }}
          >
            {block.is_visible ? "Visible" : "Hidden"}
          </button>
          {onClose && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={onClose}
              style={{ fontSize: "0.75rem", padding: "0.25rem 0.5rem" }}
            >
              Close
            </button>
          )}
        </div>
      </div>

      {/* Hero Form */}
      {block.type === "hero" && (
        <div className="space-y-3">
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>
              Headline <span style={{ color: "var(--error)" }}>*</span>
            </label>
            <input
              type="text"
              className="form-input"
              value={block.content.headline || ""}
              maxLength={160}
              placeholder="e.g. Empower Your Digital Growth"
              onChange={(e) => updateContent({ headline: e.target.value })}
            />
          </div>
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Subheadline</label>
            <textarea
              className="form-input"
              rows={2}
              value={block.content.subheadline || ""}
              maxLength={300}
              placeholder="Supporting subtitle describing your core value proposition..."
              onChange={(e) => updateContent({ subheadline: e.target.value })}
            />
          </div>
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Text Alignment</label>
            <select
              className="form-input"
              value={block.content.alignment || "left"}
              onChange={(e) => updateContent({ alignment: e.target.value })}
            >
              <option value="left">Left Aligned</option>
              <option value="center">Center Aligned</option>
              <option value="right">Right Aligned</option>
            </select>
          </div>
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Background Image URL</label>
            <input
              type="text"
              className="form-input"
              value={block.content.background_image_url || ""}
              placeholder="https://example.com/hero-bg.jpg"
              onChange={(e) => updateContent({ background_image_url: e.target.value || null })}
            />
            {validateUrl(block.content.background_image_url) && (
              <div style={{ color: "var(--error)", fontSize: "0.75rem", marginTop: "0.25rem" }}>
                {validateUrl(block.content.background_image_url)}
              </div>
            )}
          </div>

          <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.75rem", marginTop: "0.75rem" }}>
            <div style={{ fontWeight: 600, fontSize: "0.8125rem", marginBottom: "0.5rem" }}>CTA Button</div>
            <div className="space-y-2">
              <input
                type="text"
                className="form-input"
                placeholder="Button Label (e.g. Get Started)"
                maxLength={60}
                value={block.content.cta_button?.label || ""}
                onChange={(e) =>
                  updateContent({
                    cta_button: {
                      ...(block.content.cta_button || { style: "primary", target_url: "#" }),
                      label: e.target.value,
                    },
                  })
                }
              />
              <input
                type="text"
                className="form-input"
                placeholder="Target URL (e.g. /contact or https://...)"
                maxLength={500}
                value={block.content.cta_button?.target_url || ""}
                onChange={(e) =>
                  updateContent({
                    cta_button: {
                      ...(block.content.cta_button || { label: "Learn More", style: "primary" }),
                      target_url: e.target.value,
                    },
                  })
                }
              />
              {validateUrl(block.content.cta_button?.target_url) && (
                <div style={{ color: "var(--error)", fontSize: "0.75rem" }}>
                  {validateUrl(block.content.cta_button?.target_url)}
                </div>
              )}
              <select
                className="form-input"
                value={block.content.cta_button?.style || "primary"}
                onChange={(e) =>
                  updateContent({
                    cta_button: {
                      ...(block.content.cta_button || { label: "Learn More", target_url: "#" }),
                      style: e.target.value,
                    },
                  })
                }
              >
                <option value="primary">Primary Button</option>
                <option value="secondary">Secondary Button</option>
                <option value="outline">Outline Button</option>
              </select>
            </div>
          </div>
        </div>
      )}

      {/* Text Section Form */}
      {block.type === "text_section" && (
        <div className="space-y-3">
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Section Heading</label>
            <input
              type="text"
              className="form-input"
              value={block.content.heading || ""}
              maxLength={160}
              placeholder="e.g. About Our Mission"
              onChange={(e) => updateContent({ heading: e.target.value })}
            />
          </div>
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>
              Content Body (Markdown/Plain Text) <span style={{ color: "var(--error)" }}>*</span>
            </label>
            <textarea
              className="form-input"
              rows={8}
              value={block.content.body || ""}
              maxLength={10000}
              placeholder="Write rich paragraph content here. You can use markdown like **bold**, *italic*, bullet points, and headers."
              onChange={(e) => updateContent({ body: e.target.value })}
            />
          </div>
        </div>
      )}

      {/* Features Grid Form */}
      {block.type === "features_grid" && (
        <div className="space-y-3">
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Section Title</label>
            <input
              type="text"
              className="form-input"
              value={block.content.title || ""}
              maxLength={160}
              placeholder="e.g. Why Choose Our Platform"
              onChange={(e) => updateContent({ title: e.target.value })}
            />
          </div>
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.75rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
              <span style={{ fontWeight: 600, fontSize: "0.8125rem" }}>
                Feature Items ({(block.content.items || []).length}/16)
              </span>
              {(block.content.items || []).length < 16 && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => {
                    const current = block.content.items || [];
                    updateContent({
                      items: [
                        ...current,
                        {
                          title: `Feature ${current.length + 1}`,
                          description: "Describe the key value of this feature in detail.",
                          icon: "check",
                        },
                      ],
                    });
                  }}
                  style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem" }}
                >
                  + Add Feature
                </button>
              )}
            </div>

            <div className="space-y-2">
              {(block.content.items || []).map((item: any, idx: number) => (
                <div
                  key={idx}
                  style={{
                    padding: "0.5rem",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius-sm)",
                    background: "var(--bg-secondary)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.25rem" }}>
                    <span style={{ fontSize: "0.75rem", fontWeight: 600 }}>Item #{idx + 1}</span>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => {
                        const next = [...(block.content.items || [])];
                        next.splice(idx, 1);
                        updateContent({ items: next });
                      }}
                      style={{ fontSize: "0.6875rem", color: "var(--error)", padding: "0.1rem 0.4rem" }}
                    >
                      Remove
                    </button>
                  </div>
                  <input
                    type="text"
                    className="form-input"
                    style={{ marginBottom: "0.25rem", fontSize: "0.8125rem" }}
                    value={item.title || ""}
                    placeholder="Feature Title"
                    maxLength={100}
                    onChange={(e) => {
                      const next = [...(block.content.items || [])];
                      next[idx] = { ...next[idx], title: e.target.value };
                      updateContent({ items: next });
                    }}
                  />
                  <textarea
                    className="form-input"
                    rows={2}
                    style={{ fontSize: "0.75rem" }}
                    value={item.description || ""}
                    placeholder="Feature Description"
                    maxLength={500}
                    onChange={(e) => {
                      const next = [...(block.content.items || [])];
                      next[idx] = { ...next[idx], description: e.target.value };
                      updateContent({ items: next });
                    }}
                  />
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Image Gallery Form */}
      {block.type === "image_gallery" && (
        <div className="space-y-3">
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Gallery Title</label>
            <input
              type="text"
              className="form-input"
              value={block.content.title || ""}
              maxLength={160}
              placeholder="e.g. Visual Gallery"
              onChange={(e) => updateContent({ title: e.target.value })}
            />
          </div>
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.75rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
              <span style={{ fontWeight: 600, fontSize: "0.8125rem" }}>
                Images ({(block.content.images || []).length}/24)
              </span>
              {(block.content.images || []).length < 24 && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => {
                    const current = block.content.images || [];
                    updateContent({
                      images: [
                        ...current,
                        {
                          image_url: "https://images.unsplash.com/photo-1579546929518-9e396f3cc809?w=800",
                          alt_text: "Showcase photo",
                          caption: "Gallery highlight",
                        },
                      ],
                    });
                  }}
                  style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem" }}
                >
                  + Add Image
                </button>
              )}
            </div>

            <div className="space-y-2">
              {(block.content.images || []).map((img: any, idx: number) => (
                <div
                  key={idx}
                  style={{
                    padding: "0.5rem",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius-sm)",
                    background: "var(--bg-secondary)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.25rem" }}>
                    <span style={{ fontSize: "0.75rem", fontWeight: 600 }}>Image #{idx + 1}</span>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => {
                        const next = [...(block.content.images || [])];
                        next.splice(idx, 1);
                        updateContent({ images: next });
                      }}
                      style={{ fontSize: "0.6875rem", color: "var(--error)", padding: "0.1rem 0.4rem" }}
                    >
                      Remove
                    </button>
                  </div>
                  <input
                    type="text"
                    className="form-input"
                    style={{ marginBottom: "0.25rem", fontSize: "0.8125rem" }}
                    value={img.image_url || ""}
                    placeholder="Image URL"
                    maxLength={500}
                    onChange={(e) => {
                      const next = [...(block.content.images || [])];
                      next[idx] = { ...next[idx], image_url: e.target.value };
                      updateContent({ images: next });
                    }}
                  />
                  {validateUrl(img.image_url) && (
                    <div style={{ color: "var(--error)", fontSize: "0.6875rem", marginBottom: "0.25rem" }}>
                      {validateUrl(img.image_url)}
                    </div>
                  )}
                  <input
                    type="text"
                    className="form-input"
                    style={{ marginBottom: "0.25rem", fontSize: "0.8125rem" }}
                    value={img.alt_text || ""}
                    placeholder="Mandatory Alt Text (Accessibility)"
                    maxLength={200}
                    onChange={(e) => {
                      const next = [...(block.content.images || [])];
                      next[idx] = { ...next[idx], alt_text: e.target.value };
                      updateContent({ images: next });
                    }}
                  />
                  <input
                    type="text"
                    className="form-input"
                    style={{ fontSize: "0.8125rem" }}
                    value={img.caption || ""}
                    placeholder="Optional Caption"
                    maxLength={200}
                    onChange={(e) => {
                      const next = [...(block.content.images || [])];
                      next[idx] = { ...next[idx], caption: e.target.value };
                      updateContent({ images: next });
                    }}
                  />
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Contact Form Editor */}
      {block.type === "contact_form" && (
        <div className="space-y-3">
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Form Title</label>
            <input
              type="text"
              className="form-input"
              value={block.content.title || ""}
              maxLength={120}
              placeholder="e.g. Contact Us"
              onChange={(e) => updateContent({ title: e.target.value })}
            />
          </div>
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Submit Button Label</label>
            <input
              type="text"
              className="form-input"
              value={block.content.submit_label || "Send Message"}
              maxLength={60}
              placeholder="e.g. Send Message"
              onChange={(e) => updateContent({ submit_label: e.target.value })}
            />
          </div>
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Enabled Form Fields</label>
            <div className="space-y-1" style={{ fontSize: "0.8125rem" }}>
              {["name", "email", "phone", "message"].map((fieldKey) => {
                const currentFields: string[] = block.content.fields || ["name", "email", "phone", "message"];
                const isChecked = currentFields.includes(fieldKey);
                return (
                  <label key={fieldKey} style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onChange={(e) => {
                        const next = e.target.checked
                          ? [...currentFields, fieldKey]
                          : currentFields.filter((f) => f !== fieldKey);
                        updateContent({ fields: next });
                      }}
                    />
                    <span style={{ textTransform: "capitalize" }}>{fieldKey}</span>
                  </label>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* CTA Banner Editor */}
      {block.type === "cta_banner" && (
        <div className="space-y-3">
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>
              Banner Title <span style={{ color: "var(--error)" }}>*</span>
            </label>
            <input
              type="text"
              className="form-input"
              value={block.content.title || ""}
              maxLength={160}
              placeholder="e.g. Ready to Launch Your Next Project?"
              onChange={(e) => updateContent({ title: e.target.value })}
            />
          </div>
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Banner Description</label>
            <textarea
              className="form-input"
              rows={2}
              value={block.content.description || ""}
              maxLength={300}
              placeholder="Brief call-to-action details or incentive..."
              onChange={(e) => updateContent({ description: e.target.value })}
            />
          </div>
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.75rem" }}>
            <div style={{ fontWeight: 600, fontSize: "0.8125rem", marginBottom: "0.5rem" }}>Action Button</div>
            <div className="space-y-2">
              <input
                type="text"
                className="form-input"
                placeholder="Button Label"
                maxLength={60}
                value={block.content.button?.label || ""}
                onChange={(e) =>
                  updateContent({
                    button: {
                      ...(block.content.button || { style: "primary", target_url: "#" }),
                      label: e.target.value,
                    },
                  })
                }
              />
              <input
                type="text"
                className="form-input"
                placeholder="Target URL (e.g. /get-started)"
                maxLength={500}
                value={block.content.button?.target_url || ""}
                onChange={(e) =>
                  updateContent({
                    button: {
                      ...(block.content.button || { label: "Join Now", style: "primary" }),
                      target_url: e.target.value,
                    },
                  })
                }
              />
              {validateUrl(block.content.button?.target_url) && (
                <div style={{ color: "var(--error)", fontSize: "0.75rem" }}>
                  {validateUrl(block.content.button?.target_url)}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Ad Placement Editor */}
      {block.type === "ad_placement" && (
        <div className="space-y-3">
          <div
            style={{
              padding: "0.75rem",
              background: "rgba(245, 158, 11, 0.1)",
              border: "1px solid rgba(245, 158, 11, 0.3)",
              borderRadius: "var(--radius-sm)",
              fontSize: "0.75rem",
              color: "var(--text-secondary)",
              lineHeight: 1.4,
            }}
          >
            <strong>Preview Placeholder:</strong> This block designates an ad space on your website. Ads are strictly non-billable mockups during draft editing and preview. Live monetization and tracking remain disabled until publisher verification in later phases.
          </div>

          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Placement Location</label>
            <select
              className="form-input"
              value={block.content.placement_location || "in_article"}
              onChange={(e) => updateContent({ placement_location: e.target.value })}
            >
              <option value="header_banner">Header Banner (Top)</option>
              <option value="in_article">In-Article (Mid-Content)</option>
              <option value="sidebar">Sidebar</option>
              <option value="footer">Footer Banner</option>
            </select>
          </div>

          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Creative Format</label>
            <select
              className="form-input"
              value={block.content.format || 1}
              onChange={(e) => updateContent({ format: parseInt(e.target.value, 10) })}
            >
              <option value={1}>Format 1 — Display Banner (728x90 / 300x250)</option>
              <option value={2}>Format 2 — Native Card</option>
              <option value={3}>Format 3 — Video / Responsive</option>
              <option value={4}>Format 4 — Interstitial / Sticky</option>
            </select>
          </div>

          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Ad Slot Key (Optional)</label>
            <input
              type="text"
              className="form-input"
              value={block.content.ad_slot_id || ""}
              placeholder="e.g. slot_blog_sidebar_top"
              maxLength={255}
              onChange={(e) => updateContent({ ad_slot_id: e.target.value || null })}
            />
          </div>
        </div>
      )}

      {/* Footer Editor */}
      {block.type === "footer" && (
        <div className="space-y-3">
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>Copyright Text</label>
            <input
              type="text"
              className="form-input"
              value={block.content.copyright_text || ""}
              maxLength={160}
              placeholder="e.g. © 2026 BharatTech. All rights reserved."
              onChange={(e) => updateContent({ copyright_text: e.target.value })}
            />
          </div>

          <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.75rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
              <span style={{ fontWeight: 600, fontSize: "0.8125rem" }}>
                Footer Links ({(block.content.links || []).length}/8)
              </span>
              {(block.content.links || []).length < 8 && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => {
                    const current = block.content.links || [];
                    updateContent({
                      links: [
                        ...current,
                        { label: `Link ${current.length + 1}`, target_url: "#" },
                      ],
                    });
                  }}
                  style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem" }}
                >
                  + Add Link
                </button>
              )}
            </div>

            <div className="space-y-2">
              {(block.content.links || []).map((link: any, idx: number) => (
                <div
                  key={idx}
                  style={{
                    padding: "0.5rem",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius-sm)",
                    background: "var(--bg-secondary)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.25rem" }}>
                    <span style={{ fontSize: "0.75rem", fontWeight: 600 }}>Link #{idx + 1}</span>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => {
                        const next = [...(block.content.links || [])];
                        next.splice(idx, 1);
                        updateContent({ links: next });
                      }}
                      style={{ fontSize: "0.6875rem", color: "var(--error)", padding: "0.1rem 0.4rem" }}
                    >
                      Remove
                    </button>
                  </div>
                  <input
                    type="text"
                    className="form-input"
                    style={{ marginBottom: "0.25rem", fontSize: "0.8125rem" }}
                    value={link.label || ""}
                    placeholder="Link Label"
                    maxLength={50}
                    onChange={(e) => {
                      const next = [...(block.content.links || [])];
                      next[idx] = { ...next[idx], label: e.target.value };
                      updateContent({ links: next });
                    }}
                  />
                  <input
                    type="text"
                    className="form-input"
                    style={{ fontSize: "0.8125rem" }}
                    value={link.target_url || ""}
                    placeholder="Target URL (e.g. /privacy)"
                    maxLength={500}
                    onChange={(e) => {
                      const next = [...(block.content.links || [])];
                      next[idx] = { ...next[idx], target_url: e.target.value };
                      updateContent({ links: next });
                    }}
                  />
                  {validateUrl(link.target_url) && (
                    <div style={{ color: "var(--error)", fontSize: "0.6875rem", marginTop: "0.25rem" }}>
                      {validateUrl(link.target_url)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Delete Block Section */}
      <div style={{ borderTop: "1px solid var(--border)", paddingTop: "1rem", marginTop: "1.5rem" }}>
        {!confirmDelete ? (
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setConfirmDelete(true)}
            style={{ width: "100%", color: "var(--error)", fontSize: "0.8125rem" }}
          >
            Delete Block
          </button>
        ) : (
          <div style={{ textAlign: "center", padding: "0.5rem", background: "rgba(239, 68, 68, 0.08)", borderRadius: "var(--radius-sm)" }}>
            <div style={{ fontSize: "0.75rem", color: "var(--error)", marginBottom: "0.5rem" }}>
              Are you sure you want to remove this block?
            </div>
            <div style={{ display: "flex", gap: "0.5rem", justifyContent: "center" }}>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setConfirmDelete(false)}
                style={{ fontSize: "0.75rem" }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => onDelete(block.id)}
                style={{ fontSize: "0.75rem", background: "var(--error)", borderColor: "var(--error)" }}
              >
                Confirm Delete
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
