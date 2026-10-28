import React, { useState } from "react";
import { PageData, SeoMeta } from "../../types";

interface PageSettingsFormProps {
  page: PageData;
  canDelete: boolean;
  onChange: (updatedPage: PageData) => void;
  onDeletePage: (pageId: number) => void;
  onClose?: () => void;
}

const PAGE_SLUG_REGEX = /^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$/;

export const PageSettingsForm: React.FC<PageSettingsFormProps> = ({
  page,
  canDelete,
  onChange,
  onDeletePage,
  onClose,
}) => {
  const [confirmDelete, setConfirmDelete] = useState(false);

  const updateField = (partial: Partial<PageData>) => {
    onChange({
      ...page,
      ...partial,
    });
  };

  const updateSeo = (partial: Partial<SeoMeta>) => {
    onChange({
      ...page,
      seo_meta: {
        ...page.seo_meta,
        ...partial,
      },
    });
  };

  const slugError = (): string | null => {
    if (page.is_home && (!page.slug || page.slug.trim() === "")) return null;
    const clean = (page.slug || "").trim().toLowerCase();
    if (!clean) return "Non-home pages must have a URL slug";
    if (!PAGE_SLUG_REGEX.test(clean)) {
      return "Slug must contain only lowercase letters, numbers, and hyphens (no trailing hyphen)";
    }
    return null;
  };

  const titleError = (): string | null => {
    if (!page.title || page.title.trim() === "") {
      return "Page title is required";
    }
    return null;
  };

  return (
    <div className="wb-page-settings" data-testid="page-settings-form">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <div>
          <h3 style={{ fontSize: "1rem", fontWeight: 700, margin: 0 }}>Page Settings</h3>
          <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
            ID: {page.id} {page.is_home && "(Home Page)"}
          </span>
        </div>
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

      <div className="space-y-3">
        {/* Page Title */}
        <div>
          <label className="form-label" style={{ fontSize: "0.8125rem" }}>
            Page Title <span style={{ color: "var(--error)" }}>*</span>
          </label>
          <input
            type="text"
            className="form-input"
            value={page.title}
            maxLength={255}
            placeholder="e.g. Services & Solutions"
            onChange={(e) => updateField({ title: e.target.value })}
            data-testid="page-title-input"
          />
          {titleError() && (
            <div style={{ color: "var(--error)", fontSize: "0.75rem", marginTop: "0.25rem" }}>
              {titleError()}
            </div>
          )}
        </div>

        {/* Slug */}
        <div>
          <label className="form-label" style={{ fontSize: "0.8125rem" }}>
            URL Path Segment
          </label>
          <div style={{ display: "flex", alignItems: "center", gap: "0.25rem" }}>
            <span style={{ color: "var(--text-muted)", fontSize: "0.8125rem" }}>/</span>
            <input
              type="text"
              className="form-input"
              value={page.slug}
              maxLength={100}
              placeholder={page.is_home ? "(root homepage)" : "services"}
              disabled={page.is_home}
              onChange={(e) => updateField({ slug: e.target.value.toLowerCase().replace(/\s+/g, "-") })}
              data-testid="page-slug-input"
            />
          </div>
          {page.is_home ? (
            <span style={{ fontSize: "0.6875rem", color: "var(--text-muted)" }}>
              The homepage is served at the domain root (/).
            </span>
          ) : (
            slugError() && (
              <div style={{ color: "var(--error)", fontSize: "0.75rem", marginTop: "0.25rem" }}>
                {slugError()}
              </div>
            )
          )}
        </div>

        {/* Set as Home Page */}
        <div style={{ paddingTop: "0.25rem" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer", fontSize: "0.8125rem" }}>
            <input
              type="checkbox"
              checked={page.is_home}
              onChange={(e) => {
                const checked = e.target.checked;
                updateField({
                  is_home: checked,
                  slug: checked ? "" : (page.slug || "page"),
                });
              }}
              data-testid="page-is-home-checkbox"
            />
            <span style={{ fontWeight: 600 }}>Set as Default Homepage</span>
          </label>
        </div>

        {/* SEO Metadata Section */}
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.875rem", marginTop: "0.875rem" }}>
          <h4 style={{ fontSize: "0.875rem", fontWeight: 600, margin: "0 0 0.5rem 0" }}>
            SEO & Social Metadata
          </h4>

          <div className="space-y-2">
            <div>
              <label className="form-label" style={{ fontSize: "0.75rem" }}>Meta Title</label>
              <input
                type="text"
                className="form-input"
                style={{ fontSize: "0.8125rem" }}
                value={page.seo_meta?.meta_title || ""}
                maxLength={120}
                placeholder="Custom browser tab and search result title"
                onChange={(e) => updateSeo({ meta_title: e.target.value || null })}
              />
            </div>

            <div>
              <label className="form-label" style={{ fontSize: "0.75rem" }}>Meta Description</label>
              <textarea
                className="form-input"
                rows={2}
                style={{ fontSize: "0.8125rem" }}
                value={page.seo_meta?.meta_description || ""}
                maxLength={300}
                placeholder="Brief summary displayed in search engine snippets..."
                onChange={(e) => updateSeo({ meta_description: e.target.value || null })}
              />
            </div>

            <div>
              <label className="form-label" style={{ fontSize: "0.75rem" }}>Social OG Image URL</label>
              <input
                type="text"
                className="form-input"
                style={{ fontSize: "0.8125rem" }}
                value={page.seo_meta?.og_image_url || ""}
                placeholder="https://example.com/og-banner.jpg"
                onChange={(e) => updateSeo({ og_image_url: e.target.value || null })}
              />
            </div>

            <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer", fontSize: "0.75rem", marginTop: "0.5rem" }}>
              <input
                type="checkbox"
                checked={!!page.seo_meta?.noindex}
                onChange={(e) => updateSeo({ noindex: e.target.checked })}
              />
              <span>Prevent Search Indexing (noindex header)</span>
            </label>
          </div>
        </div>

        {/* Delete Page */}
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: "1rem", marginTop: "1rem" }}>
          {page.is_home ? (
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", fontStyle: "italic" }}>
              The primary homepage cannot be deleted. Assign another page as home first if you wish to remove it.
            </div>
          ) : !canDelete ? (
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", fontStyle: "italic" }}>
              At least one page must exist in your website project.
            </div>
          ) : !confirmDelete ? (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setConfirmDelete(true)}
              style={{ width: "100%", color: "var(--error)", fontSize: "0.8125rem" }}
              data-testid="delete-page-btn"
            >
              Delete Page
            </button>
          ) : (
            <div style={{ textAlign: "center", padding: "0.5rem", background: "rgba(239, 68, 68, 0.08)", borderRadius: "var(--radius-sm)" }}>
              <div style={{ fontSize: "0.75rem", color: "var(--error)", marginBottom: "0.5rem" }}>
                Delete this entire page and its blocks?
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
                  onClick={() => onDeletePage(page.id)}
                  style={{ fontSize: "0.75rem", background: "var(--error)", borderColor: "var(--error)" }}
                  data-testid="confirm-delete-page-btn"
                >
                  Confirm Delete
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
