import React, { useState, useEffect } from "react";
import { WebsiteTemplateDetail, WebsiteTemplateSummary, BlockItem } from "../../types";
import { getWebsiteTemplate } from "../../api/client";
import { CanvasBlockRenderer } from "./CanvasBlockRenderer";

interface TemplatePreviewModalProps {
  isOpen: boolean;
  templateSummary: WebsiteTemplateSummary | null;
  onClose: () => void;
  onSelectTemplate: (templateId: string) => void;
}

export const TemplatePreviewModal: React.FC<TemplatePreviewModalProps> = ({
  isOpen,
  templateSummary,
  onClose,
  onSelectTemplate,
}) => {
  const [viewport, setViewport] = useState<"desktop" | "tablet" | "mobile">("desktop");
  const [detail, setDetail] = useState<WebsiteTemplateDetail | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen || !templateSummary) {
      setDetail(null);
      return;
    }
    loadTemplateDetail(templateSummary.id);
  }, [isOpen, templateSummary]);

  const loadTemplateDetail = async (id: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await getWebsiteTemplate(id);
      setDetail(data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || "Failed to load template preview");
    } finally {
      setIsLoading(false);
    }
  };

  if (!isOpen || !templateSummary) return null;

  const currentTheme = detail?.default_theme_config || templateSummary.default_theme_config;
  const blocks: BlockItem[] = detail?.default_pages?.[0]?.blocks?.blocks || [];

  const viewportWidths = {
    desktop: "1200px",
    tablet: "768px",
    mobile: "375px",
  };

  return (
    <div className="modal-overlay" style={{ zIndex: 1100, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div
        className="modal-container"
        style={{
          width: "95vw",
          maxWidth: "1400px",
          height: "92vh",
          display: "flex",
          flexDirection: "column",
          borderRadius: "12px",
          overflow: "hidden",
          backgroundColor: "#ffffff",
          boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.25)",
        }}
      >
        {/* Header Bar */}
        <div
          style={{
            padding: "1rem 1.5rem",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            backgroundColor: "#f8fafc",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
            <h3 style={{ margin: 0, fontSize: "1.125rem", fontWeight: 700, color: "#0f172a" }}>
              {templateSummary.name}
            </h3>
            <span
              style={{
                fontSize: "0.75rem",
                padding: "2px 8px",
                borderRadius: "999px",
                backgroundColor: "rgba(37, 99, 235, 0.1)",
                color: "var(--primary)",
                fontWeight: 600,
              }}
            >
              {templateSummary.category}
            </span>
            {templateSummary.badge && (
              <span
                style={{
                  fontSize: "0.75rem",
                  padding: "2px 8px",
                  borderRadius: "999px",
                  backgroundColor: "#fef3c7",
                  color: "#92400e",
                  fontWeight: 600,
                }}
              >
                {templateSummary.badge}
              </span>
            )}
          </div>

          {/* Viewport Switcher Controls */}
          <div
            style={{
              display: "flex",
              backgroundColor: "#e2e8f0",
              padding: "3px",
              borderRadius: "8px",
              gap: "2px",
            }}
          >
            <button
              type="button"
              className={`btn btn-sm ${viewport === "desktop" ? "btn-primary" : "btn-secondary"}`}
              style={{
                fontSize: "0.75rem",
                padding: "0.25rem 0.75rem",
                border: "none",
                display: "flex",
                alignItems: "center",
                gap: "4px",
              }}
              onClick={() => setViewport("desktop")}
              data-testid="viewport-desktop"
            >
              🖥️ Desktop (1200px)
            </button>
            <button
              type="button"
              className={`btn btn-sm ${viewport === "tablet" ? "btn-primary" : "btn-secondary"}`}
              style={{
                fontSize: "0.75rem",
                padding: "0.25rem 0.75rem",
                border: "none",
                display: "flex",
                alignItems: "center",
                gap: "4px",
              }}
              onClick={() => setViewport("tablet")}
              data-testid="viewport-tablet"
            >
              📱 Tablet (768px)
            </button>
            <button
              type="button"
              className={`btn btn-sm ${viewport === "mobile" ? "btn-primary" : "btn-secondary"}`}
              style={{
                fontSize: "0.75rem",
                padding: "0.25rem 0.75rem",
                border: "none",
                display: "flex",
                alignItems: "center",
                gap: "4px",
              }}
              onClick={() => setViewport("mobile")}
              data-testid="viewport-mobile"
            >
              📱 Mobile (375px)
            </button>
          </div>

          {/* Action Buttons */}
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              style={{ fontWeight: 600, padding: "0.4rem 1.25rem" }}
              onClick={() => {
                onSelectTemplate(templateSummary.id);
                onClose();
              }}
              data-testid="use-template-btn"
            >
              Use This Template
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={onClose}
              data-testid="close-preview-btn"
            >
              ✕ Close
            </button>
          </div>
        </div>

        {/* Preview Frame Canvas */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            backgroundColor: "#0f172a",
            padding: "2rem 1rem",
            display: "flex",
            justifyContent: "center",
            alignItems: "flex-start",
          }}
        >
          {isLoading ? (
            <div style={{ color: "#ffffff", padding: "4rem", textAlign: "center" }}>
              <div className="spinner" style={{ margin: "0 auto 1rem" }} />
              <p>Loading template layout and design tokens...</p>
            </div>
          ) : error ? (
            <div
              style={{
                backgroundColor: "#ffffff",
                padding: "2rem",
                borderRadius: "8px",
                maxWidth: "500px",
                textAlign: "center",
              }}
            >
              <h4 style={{ color: "var(--error)", margin: "0 0 0.5rem" }}>Failed to Load Preview</h4>
              <p style={{ color: "#64748b", fontSize: "0.875rem" }}>{error}</p>
            </div>
          ) : (
            <div
              className={`od-viewport-wrapper od-viewport-${viewport}`}
              style={{
                width: viewportWidths[viewport],
                maxWidth: "100%",
                minHeight: "600px",
                backgroundColor: currentTheme.background_color || "#ffffff",
                borderRadius: viewport !== "desktop" ? "12px" : "0",
                boxShadow: "0 25px 50px rgba(0, 0, 0, 0.4)",
                overflow: "hidden",
                transition: "width 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
              }}
            >
              {blocks.length === 0 ? (
                <div style={{ padding: "3rem", textAlign: "center", color: "#64748b" }}>
                  <p>No blocks available for preview.</p>
                </div>
              ) : (
                blocks.map((blk) => (
                  <CanvasBlockRenderer
                    key={blk.id}
                    block={blk}
                    theme={currentTheme}
                    isSelected={false}
                    onSelect={() => {}}
                    onDelete={() => {}}
                    canMoveUp={false}
                    canMoveDown={false}
                  />
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

