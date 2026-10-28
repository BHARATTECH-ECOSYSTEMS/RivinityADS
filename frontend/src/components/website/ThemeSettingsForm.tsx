import React from "react";
import { ThemeConfig } from "../../types";

interface ThemeSettingsFormProps {
  projectName: string;
  theme: ThemeConfig;
  onProjectNameChange: (name: string) => void;
  onChange: (updatedTheme: ThemeConfig) => void;
  onClose?: () => void;
}

const ALLOWED_FONTS = [
  "Inter",
  "Roboto",
  "Open Sans",
  "Lato",
  "Poppins",
  "Montserrat",
  "Playfair Display",
  "Merriweather",
  "system-ui",
];

const PRESET_PALETTES = [
  {
    name: "Modern Indigo",
    primary: "#2563eb",
    secondary: "#475569",
    background: "#ffffff",
    text: "#0f172a",
  },
  {
    name: "Emerald Tech",
    primary: "#059669",
    secondary: "#334155",
    background: "#ffffff",
    text: "#0f172a",
  },
  {
    name: "Midnight Luxury",
    primary: "#6366f1",
    secondary: "#94a3b8",
    background: "#0f172a",
    text: "#f8fafc",
  },
  {
    name: "Warm Amber",
    primary: "#d97706",
    secondary: "#52525b",
    background: "#fffbeb",
    text: "#1c1917",
  },
  {
    name: "Clean Slate",
    primary: "#334155",
    secondary: "#64748b",
    background: "#f8fafc",
    text: "#020617",
  },
];

export const ThemeSettingsForm: React.FC<ThemeSettingsFormProps> = ({
  projectName,
  theme,
  onProjectNameChange,
  onChange,
  onClose,
}) => {
  const updateField = (partial: Partial<ThemeConfig>) => {
    onChange({
      ...theme,
      ...partial,
    });
  };

  const applyPreset = (preset: typeof PRESET_PALETTES[0]) => {
    updateField({
      primary_color: preset.primary,
      secondary_color: preset.secondary,
      background_color: preset.background,
      text_color: preset.text,
    });
  };

  return (
    <div className="wb-theme-settings" data-testid="theme-settings-form">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <div>
          <h3 style={{ fontSize: "1rem", fontWeight: 700, margin: 0 }}>Site & Theme Design</h3>
          <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>Global typography, palette & brand</span>
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

      <div className="space-y-4">
        {/* Project Name */}
        <div>
          <label className="form-label" style={{ fontSize: "0.8125rem" }}>
            Site Brand Name <span style={{ color: "var(--error)" }}>*</span>
          </label>
          <input
            type="text"
            className="form-input"
            value={projectName}
            maxLength={255}
            placeholder="e.g. Acme Tech Solutions"
            onChange={(e) => onProjectNameChange(e.target.value)}
            data-testid="theme-site-name-input"
          />
        </div>

        {/* Quick Presets */}
        <div>
          <label className="form-label" style={{ fontSize: "0.8125rem", marginBottom: "0.375rem" }}>
            Quick Palettes
          </label>
          <div style={{ display: "flex", gap: "0.375rem", flexWrap: "wrap" }}>
            {PRESET_PALETTES.map((preset) => (
              <button
                key={preset.name}
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => applyPreset(preset)}
                style={{
                  fontSize: "0.75rem",
                  padding: "0.2rem 0.5rem",
                  display: "flex",
                  alignItems: "center",
                  gap: "0.375rem",
                }}
              >
                <span
                  style={{
                    width: "10px",
                    height: "10px",
                    borderRadius: "50%",
                    backgroundColor: preset.primary,
                    display: "inline-block",
                  }}
                />
                {preset.name}
              </button>
            ))}
          </div>
        </div>

        {/* Color Controls */}
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.875rem" }}>
          <h4 style={{ fontSize: "0.875rem", fontWeight: 600, margin: "0 0 0.5rem 0" }}>Colors</h4>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.625rem" }}>
            <div>
              <label className="form-label" style={{ fontSize: "0.75rem" }}>Primary Brand</label>
              <div style={{ display: "flex", alignItems: "center", gap: "0.375rem" }}>
                <input
                  type="color"
                  value={theme.primary_color || "#2563eb"}
                  onChange={(e) => updateField({ primary_color: e.target.value })}
                  style={{ width: "32px", height: "32px", padding: 0, border: "none", cursor: "pointer", borderRadius: "4px" }}
                />
                <input
                  type="text"
                  className="form-input"
                  style={{ fontSize: "0.75rem", padding: "0.25rem 0.375rem" }}
                  value={theme.primary_color || "#2563eb"}
                  onChange={(e) => updateField({ primary_color: e.target.value })}
                />
              </div>
            </div>

            <div>
              <label className="form-label" style={{ fontSize: "0.75rem" }}>Secondary Accent</label>
              <div style={{ display: "flex", alignItems: "center", gap: "0.375rem" }}>
                <input
                  type="color"
                  value={theme.secondary_color || "#475569"}
                  onChange={(e) => updateField({ secondary_color: e.target.value })}
                  style={{ width: "32px", height: "32px", padding: 0, border: "none", cursor: "pointer", borderRadius: "4px" }}
                />
                <input
                  type="text"
                  className="form-input"
                  style={{ fontSize: "0.75rem", padding: "0.25rem 0.375rem" }}
                  value={theme.secondary_color || "#475569"}
                  onChange={(e) => updateField({ secondary_color: e.target.value })}
                />
              </div>
            </div>

            <div>
              <label className="form-label" style={{ fontSize: "0.75rem" }}>Background</label>
              <div style={{ display: "flex", alignItems: "center", gap: "0.375rem" }}>
                <input
                  type="color"
                  value={theme.background_color || "#ffffff"}
                  onChange={(e) => updateField({ background_color: e.target.value })}
                  style={{ width: "32px", height: "32px", padding: 0, border: "none", cursor: "pointer", borderRadius: "4px" }}
                />
                <input
                  type="text"
                  className="form-input"
                  style={{ fontSize: "0.75rem", padding: "0.25rem 0.375rem" }}
                  value={theme.background_color || "#ffffff"}
                  onChange={(e) => updateField({ background_color: e.target.value })}
                />
              </div>
            </div>

            <div>
              <label className="form-label" style={{ fontSize: "0.75rem" }}>Default Text</label>
              <div style={{ display: "flex", alignItems: "center", gap: "0.375rem" }}>
                <input
                  type="color"
                  value={theme.text_color || "#0f172a"}
                  onChange={(e) => updateField({ text_color: e.target.value })}
                  style={{ width: "32px", height: "32px", padding: 0, border: "none", cursor: "pointer", borderRadius: "4px" }}
                />
                <input
                  type="text"
                  className="form-input"
                  style={{ fontSize: "0.75rem", padding: "0.25rem 0.375rem" }}
                  value={theme.text_color || "#0f172a"}
                  onChange={(e) => updateField({ text_color: e.target.value })}
                />
              </div>
            </div>
          </div>
        </div>

        {/* Typography & Shape */}
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.875rem" }}>
          <h4 style={{ fontSize: "0.875rem", fontWeight: 600, margin: "0 0 0.5rem 0" }}>Typography & Corners</h4>
          <div className="space-y-2">
            <div>
              <label className="form-label" style={{ fontSize: "0.75rem" }}>Font Family</label>
              <select
                className="form-input"
                value={theme.font_family || "Inter"}
                onChange={(e) => updateField({ font_family: e.target.value })}
                data-testid="theme-font-family-select"
              >
                {ALLOWED_FONTS.map((font) => (
                  <option key={font} value={font} style={{ fontFamily: font }}>
                    {font}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="form-label" style={{ fontSize: "0.75rem" }}>Button & Card Corner Radius</label>
              <select
                className="form-input"
                value={theme.border_radius || "0.5rem"}
                onChange={(e) => updateField({ border_radius: e.target.value })}
              >
                <option value="0rem">Sharp (0px)</option>
                <option value="0.25rem">Subtle (4px)</option>
                <option value="0.5rem">Standard (8px)</option>
                <option value="0.75rem">Curved (12px)</option>
                <option value="1rem">Pill / Rounded (16px)</option>
              </select>
            </div>
          </div>
        </div>

        {/* Logo and Assets */}
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.875rem" }}>
          <h4 style={{ fontSize: "0.875rem", fontWeight: 600, margin: "0 0 0.5rem 0" }}>Branding Assets</h4>
          <div className="space-y-2">
            <div>
              <label className="form-label" style={{ fontSize: "0.75rem" }}>Header Logo Image URL</label>
              <input
                type="text"
                className="form-input"
                style={{ fontSize: "0.8125rem" }}
                value={theme.logo_url || ""}
                placeholder="https://example.com/logo.svg"
                onChange={(e) => updateField({ logo_url: e.target.value || null })}
              />
            </div>
            <div>
              <label className="form-label" style={{ fontSize: "0.75rem" }}>Browser Favicon URL</label>
              <input
                type="text"
                className="form-input"
                style={{ fontSize: "0.8125rem" }}
                value={theme.favicon_url || ""}
                placeholder="https://example.com/favicon.ico"
                onChange={(e) => updateField({ favicon_url: e.target.value || null })}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
