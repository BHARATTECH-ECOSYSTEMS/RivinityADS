import React, { useState } from "react";
import { BlockItem, AISectionPreset } from "../../types";
import { generateWebsiteAISection, ApiError } from "../../api/client";
import { validateGeneratedBlockItem } from "../../services/openDesignPatcher";

export interface AISectionGeneratorModalProps {
  isOpen: boolean;
  projectId: number;
  onClose: () => void;
  onSectionGenerated: (
    block: BlockItem,
    meta?: { quotaRemaining: number; dailyLimit: number; resetTimestampUtc: string }
  ) => void;
  initialQuota?: { quotaRemaining: number; dailyLimit: number; resetTimestampUtc: string } | null;
}

export const PRESET_OPTIONS: Array<{ value: AISectionPreset; label: string; icon: string; hint: string }> = [
  { value: "hero", label: "Hero Banner", icon: "🚀", hint: "High-impact headline, subhead, CTA button" },
  { value: "features", label: "Features Grid", icon: "⚡", hint: "Multi-column capability cards with icons" },
  { value: "testimonials", label: "Testimonials", icon: "💬", hint: "Customer quotes, ratings, avatar reviews" },
  { value: "pricing", label: "Pricing Table", icon: "💳", hint: "Tiered pricing tiers, feature lists, CTA" },
  { value: "cta", label: "Call to Action", icon: "🎯", hint: "Bold banner focused on single conversion action" },
  { value: "stats", label: "Stats & Metrics", icon: "📊", hint: "Key numeric indicators and milestone metrics" },
  { value: "faq", label: "FAQ Accordion", icon: "❓", hint: "Frequently asked questions with clear answers" },
  { value: "gallery", label: "Media Showcase", icon: "🖼️", hint: "Visual image showcase with captions" },
  { value: "about", label: "About / Story", icon: "👥", hint: "Company vision, mission, and team summary" },
  { value: "contact", label: "Contact / Inquiry", icon: "✉️", hint: "Direct inquiry form and contact options" },
  { value: "footer", label: "Footer Links", icon: "⚓", hint: "Navigation sitemap links and legal notice" },
  { value: "custom", label: "Custom Section", icon: "🎨", hint: "Freeform section according to prompt description" },
];

export const AISectionGeneratorModal: React.FC<AISectionGeneratorModalProps> = ({
  isOpen,
  projectId,
  onClose,
  onSectionGenerated,
  initialQuota = null,
}) => {
  const [prompt, setPrompt] = useState("");
  const [sectionType, setSectionType] = useState<AISectionPreset>("hero");
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quotaInfo, setQuotaInfo] = useState<{
    remaining: number;
    limit: number;
    resetUtc: string;
  } | null>(
    initialQuota
      ? {
        remaining: initialQuota.quotaRemaining,
        limit: initialQuota.dailyLimit,
        resetUtc: initialQuota.resetTimestampUtc,
      }
      : null
  );

  if (!isOpen) return null;

  const trimmedPrompt = prompt.trim();
  const promptLength = trimmedPrompt.length;
  const isPromptValid = promptLength >= 3 && promptLength <= 1000;

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isGenerating) return;

    if (!isPromptValid) {
      if (promptLength < 3) {
        setError("Prompt must be at least 3 characters long.");
      } else {
        setError("Prompt cannot exceed 1000 characters.");
      }
      return;
    }

    setIsGenerating(true);
    setError(null);

    try {
      const response = await generateWebsiteAISection(projectId, {
        prompt: trimmedPrompt,
        section_type: sectionType,
      });

      // Strict client-side validation of response contract using Schema v2 rules
      if (!response || typeof response !== "object") {
        throw new Error("Invalid response received from server.");
      }
      if (!response.block || typeof response.block !== "object") {
        throw new Error("Server response is missing the generated block item.");
      }

      const validation = validateGeneratedBlockItem(response.block);
      if (!validation.isValid || !validation.validatedBlock) {
        throw new Error(validation.error || "Generated block failed validation.");
      }
      const validatedBlock = validation.validatedBlock;

      // Update quota info from backend response
      if (typeof response.quota_remaining === "number" && typeof response.daily_limit === "number") {
        setQuotaInfo({
          remaining: response.quota_remaining,
          limit: response.daily_limit,
          resetUtc: response.reset_timestamp_utc || "",
        });
      }

      // Deliver validated block to draft
      onSectionGenerated(validatedBlock, {
        quotaRemaining: response.quota_remaining,
        dailyLimit: response.daily_limit,
        resetTimestampUtc: response.reset_timestamp_utc,
      });

      onClose();
    } catch (err: any) {
      let friendlyMessage = "Failed to generate section. Please try again.";

      if (err instanceof ApiError) {
        if (err.status === 401) {
          friendlyMessage = "Your session has expired. Please log in again to generate content.";
        } else if (err.status === 403) {
          friendlyMessage = err.message || "You do not have permission to generate content for this project.";
        } else if (err.status === 404) {
          friendlyMessage = "Website project not found or access denied.";
        } else if (err.status === 422) {
          friendlyMessage = `Validation error: ${err.message}`;
        } else if (err.status === 429) {
          const retryAfter = err.headers?.get("Retry-After");
          if (err.message.toLowerCase().includes("burst")) {
            friendlyMessage = retryAfter
              ? `Burst rate limit reached (max 5/min). Please wait ${retryAfter}s before retrying.`
              : "Burst rate limit reached. Please wait a moment before trying again.";
          } else {
            friendlyMessage = "Daily generation limit reached (50 requests/day). Your quota will reset at 00:00 UTC.";
          }
        } else if (err.status === 502) {
          friendlyMessage =
            "The AI generated content that failed security policy validation. Please refine your prompt.";
        } else if (err.status === 503) {
          friendlyMessage =
            "The AI service is temporarily unavailable or timed out. Please try again in a moment.";
        } else {
          friendlyMessage = err.message || friendlyMessage;
        }
      } else if (err.message) {
        friendlyMessage = err.message;
      }

      setError(friendlyMessage);
    } finally {
      setIsGenerating(false);
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        backgroundColor: "rgba(0, 0, 0, 0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "1rem",
        backdropFilter: "blur(3px)",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && !isGenerating) {
          onClose();
        }
      }}
      data-testid="ai-generator-modal-overlay"
    >
      <div
        className="card"
        style={{
          width: "100%",
          maxWidth: "640px",
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
          padding: "1.75rem",
          boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.35)",
          borderRadius: "var(--radius-lg, 12px)",
          overflow: "hidden",
        }}
        data-testid="ai-generator-modal"
      >
        {/* Modal Header */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            marginBottom: "1.25rem",
            borderBottom: "1px solid var(--border)",
            paddingBottom: "1rem",
          }}
        >
          <div>
            <h3
              style={{
                fontSize: "1.25rem",
                fontWeight: 700,
                margin: 0,
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
              }}
            >
              <span>✨</span>
              <span>Generate Section with AI</span>
            </h3>
            <p style={{ fontSize: "0.8125rem", color: "var(--text-muted)", margin: "0.25rem 0 0" }}>
              Crafted with Open Design rules and tailored to your project’s brand tokens.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onClose}
            disabled={isGenerating}
            style={{ padding: "0.25rem 0.5rem" }}
            aria-label="Close AI Generator"
            data-testid="ai-generator-close-btn"
          >
            ✕
          </button>
        </div>

        {/* Modal Body */}
        <form
          onSubmit={handleGenerate}
          style={{
            flex: 1,
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: "1.25rem",
          }}
        >
          {/* Preset Selector */}
          <div>
            <label
              htmlFor="ai-section-preset"
              style={{
                display: "block",
                fontSize: "0.8125rem",
                fontWeight: 600,
                marginBottom: "0.5rem",
              }}
            >
              Section Preset
            </label>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(130px, 1fr))",
                gap: "0.5rem",
              }}
              role="radiogroup"
              aria-label="Section Preset Choices"
            >
              {PRESET_OPTIONS.map((opt) => {
                const isSelected = sectionType === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setSectionType(opt.value)}
                    disabled={isGenerating}
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "flex-start",
                      padding: "0.5rem 0.75rem",
                      borderRadius: "var(--radius-sm, 6px)",
                      border: isSelected ? "2px solid var(--primary)" : "1px solid var(--border)",
                      backgroundColor: isSelected ? "rgba(37, 99, 235, 0.08)" : "var(--bg-card)",
                      cursor: isGenerating ? "not-allowed" : "pointer",
                      textAlign: "left",
                      transition: "all 0.15s ease",
                    }}
                    data-testid={`preset-option-${opt.value}`}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
                      <span style={{ fontSize: "1rem" }}>{opt.icon}</span>
                      <span
                        style={{
                          fontSize: "0.75rem",
                          fontWeight: isSelected ? 700 : 500,
                          color: isSelected ? "var(--primary)" : "var(--text-primary)",
                        }}
                      >
                        {opt.label}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Prompt Textarea */}
          <div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "0.5rem",
              }}
            >
              <label
                htmlFor="ai-section-prompt"
                style={{ fontSize: "0.8125rem", fontWeight: 600, margin: 0 }}
              >
                Generation Instructions
              </label>
              <span
                style={{
                  fontSize: "0.75rem",
                  color:
                    promptLength > 1000
                      ? "var(--error)"
                      : promptLength < 3 && promptLength > 0
                        ? "var(--warning)"
                        : "var(--text-muted)",
                  fontFamily: "monospace",
                }}
                data-testid="ai-prompt-counter"
              >
                {promptLength} / 1000 characters
              </span>
            </div>
            <textarea
              id="ai-section-prompt"
              rows={4}
              className="input"
              value={prompt}
              onChange={(e) => {
                setPrompt(e.target.value);
                if (error) setError(null);
              }}
              disabled={isGenerating}
              placeholder="e.g. Design a modern, clean features grid with 3 cards highlighting security, sub-second latency, and global reach. Include subtle borders and concise copy."
              style={{
                width: "100%",
                resize: "vertical",
                fontSize: "0.875rem",
                lineHeight: "1.4",
                padding: "0.625rem",
                borderRadius: "var(--radius-sm, 6px)",
              }}
              data-testid="ai-section-prompt-input"
            />
            <div style={{ fontSize: "0.6875rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
              Enter 3 to 1000 characters. Anti-slop filters ensure zero generic purple gradients, emoji clutter, or fake metrics.
            </div>
          </div>

          {/* Quota Information Banner (only displayed when metadata is present) */}
          {quotaInfo && (
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "0.5rem 0.75rem",
                backgroundColor: "rgba(16, 185, 129, 0.08)",
                border: "1px solid rgba(16, 185, 129, 0.2)",
                borderRadius: "var(--radius-sm, 6px)",
                fontSize: "0.75rem",
              }}
              data-testid="ai-quota-info-banner"
            >
              <div>
                <strong>Daily Quota:</strong> {quotaInfo.remaining} of {quotaInfo.limit} generations remaining
              </div>
              <div style={{ color: "var(--text-muted)" }}>Resets 00:00 UTC</div>
            </div>
          )}

          {/* Error Message */}
          {error && (
            <div
              style={{
                padding: "0.625rem 0.75rem",
                backgroundColor: "rgba(239, 68, 68, 0.1)",
                border: "1px solid rgba(239, 68, 68, 0.3)",
                borderRadius: "var(--radius-sm, 6px)",
                color: "var(--error)",
                fontSize: "0.8125rem",
              }}
              data-testid="ai-generator-error-banner"
              role="alert"
            >
              {error}
            </div>
          )}

          {/* Modal Actions */}
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              gap: "0.75rem",
              borderTop: "1px solid var(--border)",
              paddingTop: "1rem",
              marginTop: "auto",
            }}
          >
            <button
              type="button"
              className="btn btn-secondary"
              onClick={onClose}
              disabled={isGenerating}
              data-testid="ai-generator-cancel-btn"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={isGenerating || !isPromptValid}
              style={{
                minWidth: "150px",
                background: isPromptValid && !isGenerating
                  ? "linear-gradient(135deg, var(--primary) 0%, #7c3aed 100%)"
                  : undefined,
                borderColor: "transparent",
              }}
              data-testid="ai-generator-submit-btn"
            >
              {isGenerating ? "Generating..." : "✨ Generate Section"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

