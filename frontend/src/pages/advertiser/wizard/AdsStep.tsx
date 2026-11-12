import { useState, useEffect, type FormEvent } from "react";
import {
  createCreative,
  listAdvertiserDomains,
  type AdvertiserDomain,
} from "../../../api/client";
import {
  AD_CATEGORIES,
  AI_CHATBOT_FORMATS,
  BANNER_FORMAT,
  CREATIVE_FORMAT_LABELS,
  type Campaign,
  type AdSet,
  type Creative,
} from "../../../types";
import { Button, Input, Select, Alert, Card } from "../../../components/ui";

interface AdsStepProps {
  campaign: Campaign;
  adSets: AdSet[];
  creatives: Creative[];
  selectedAdSetId: number | null;
  onCreativesUpdated: (creatives: Creative[]) => void;
  onBack: () => void;
  onContinue: () => void;
}

interface FormatConfig {
  id: string;
  name: string;
  category: "ai_chatbot" | "banner";
  width: number;
  height: number;
  creativeType: number;
  badge: string;
  placeholderText: string;
}

const FORMAT_CONFIGS: Record<string, FormatConfig> = {
  banner: {
    id: BANNER_FORMAT.id,
    name: "Banner Ad",
    category: "banner",
    width: BANNER_FORMAT.width,
    height: BANNER_FORMAT.height,
    creativeType: BANNER_FORMAT.typeId,
    badge: BANNER_FORMAT.label,
    placeholderText: "Banner Ad (500x500)",
  },
  display_card: {
    id: AI_CHATBOT_FORMATS.DISPLAY_CARD.id,
    name: AI_CHATBOT_FORMATS.DISPLAY_CARD.label,
    category: "ai_chatbot",
    width: AI_CHATBOT_FORMATS.DISPLAY_CARD.width,
    height: AI_CHATBOT_FORMATS.DISPLAY_CARD.height,
    creativeType: AI_CHATBOT_FORMATS.DISPLAY_CARD.typeId,
    badge: "Display Card (520 × 120)",
    placeholderText: "Display Card (520x120)",
  },
  label_ad: {
    id: AI_CHATBOT_FORMATS.LABEL_ADS.id,
    name: AI_CHATBOT_FORMATS.LABEL_ADS.label,
    category: "ai_chatbot",
    width: AI_CHATBOT_FORMATS.LABEL_ADS.width,
    height: AI_CHATBOT_FORMATS.LABEL_ADS.height,
    creativeType: AI_CHATBOT_FORMATS.LABEL_ADS.typeId,
    badge: "Label Ads (400 × 100)",
    placeholderText: "Label Ads (400x100)",
  },
  element_ad: {
    id: AI_CHATBOT_FORMATS.ELEMENT_ADS.id,
    name: AI_CHATBOT_FORMATS.ELEMENT_ADS.label,
    category: "ai_chatbot",
    width: AI_CHATBOT_FORMATS.ELEMENT_ADS.width,
    height: AI_CHATBOT_FORMATS.ELEMENT_ADS.height,
    creativeType: AI_CHATBOT_FORMATS.ELEMENT_ADS.typeId,
    badge: "Element Ads (310 × 150)",
    placeholderText: "Element Ads (310x150)",
  },
};

export function AdsStep({
  campaign,
  adSets,
  creatives,
  selectedAdSetId,
  onCreativesUpdated,
  onBack,
  onContinue,
}: AdsStepProps) {
  const [activeAdSetId, setActiveAdSetId] = useState<number>(
    selectedAdSetId || (adSets.length > 0 ? adSets[0].id : 0)
  );

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [domains, setDomains] = useState<AdvertiserDomain[]>([]);

  // Category & Format selection state
  const [category, setCategory] = useState<string>(""); // "ai_chatbot" | "banner" | ""
  const [subcategory, setSubcategory] = useState<string>(""); // "display_card" | "label_ad" | "element_ad" | ""

  // Form field state
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [imageUrl, setImageUrl] = useState("https://placehold.co/500x500.png?text=Creative+Asset");
  const [videoUrl, setVideoUrl] = useState("");
  const [landingUrl, setLandingUrl] = useState("https://example.com");

  useEffect(() => {
    void listAdvertiserDomains().then(setDomains).catch(() => {});
  }, []);

  // Compute active format configuration
  const activeFormatKey =
    category === "banner"
      ? "banner"
      : category === "ai_chatbot" && subcategory
      ? subcategory
      : null;

  const activeFormat: FormatConfig | null = activeFormatKey
    ? FORMAT_CONFIGS[activeFormatKey] || null
    : null;

  // Handle category change: switches category, immediately clears incompatible subcategory and stale dimensions
  const handleCategoryChange = (newCategory: string) => {
    setCategory(newCategory);
    setSubcategory(""); // Mandatory: clear previously selected subcategory
    setError(null);

    if (newCategory === "banner") {
      const bannerCfg = FORMAT_CONFIGS.banner;
      if (imageUrl.includes("placehold.co")) {
        setImageUrl(`https://placehold.co/${bannerCfg.width}x${bannerCfg.height}.png?text=${encodeURIComponent(bannerCfg.placeholderText)}`);
      }
    }
  };

  // Handle AI & Chatbot subcategory change
  const handleSubcategoryChange = (newSubcategory: string) => {
    setSubcategory(newSubcategory);
    setError(null);

    const cfg = FORMAT_CONFIGS[newSubcategory];
    if (cfg && imageUrl.includes("placehold.co")) {
      setImageUrl(`https://placehold.co/${cfg.width}x${cfg.height}.png?text=${encodeURIComponent(cfg.placeholderText)}`);
    }
  };

  // Domain check for landing URL
  const checkDomainVerification = (url: string) => {
    try {
      const parsed = new URL(url);
      const host = parsed.hostname.toLowerCase();
      const verified = domains.some(
        (d) => d.status === "VERIFIED" && (d.domain.toLowerCase() === host || host.endsWith("." + d.domain.toLowerCase()))
      );
      return { host, verified };
    } catch {
      return { host: "", verified: false };
    }
  };

  const domainStatus = checkDomainVerification(landingUrl);

  const handleCreateAd = async (e: FormEvent) => {
    e.preventDefault();
    if (loading) return;

    if (!activeAdSetId) {
      setError("Please select an Ad Set to assign this creative to.");
      return;
    }
    if (!category) {
      setError("Please select an Ad Category.");
      return;
    }
    if (category === "ai_chatbot" && !subcategory) {
      setError("Please select an AI and Chatbot ad format.");
      return;
    }
    if (!activeFormat) {
      setError("Please select a valid ad format.");
      return;
    }
    if (!title.trim()) {
      setError("Ad title is required.");
      return;
    }
    if (!landingUrl.startsWith("https://")) {
      setError("Landing URL must begin with secure https://");
      return;
    }
    if (imageUrl && !imageUrl.startsWith("https://")) {
      setError("Image asset URL must begin with secure https://");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const newCreative = await createCreative({
        campaign_id: campaign.id,
        ad_set_id: activeAdSetId,
        title: title.trim(),
        description: description.trim() || undefined,
        category: activeFormat.category,
        format: activeFormat.id,
        creative_type: activeFormat.creativeType,
        image_url: imageUrl.trim() || undefined,
        video_url: videoUrl.trim() || undefined,
        landing_url: landingUrl.trim(),
        width: activeFormat.width,
        height: activeFormat.height,
      });

      const updatedCreatives = [...creatives, newCreative];
      onCreativesUpdated(updatedCreatives);

      // Reset headline and description while preserving category choice
      setTitle("");
      setDescription("");
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create ad");
    } finally {
      setLoading(false);
    }
  };

  const formatModerationBadge = (c: Creative) => {
    if (c.moderation_status === 2) {
      return <span className="chip chip-include">Approved</span>;
    }
    if (c.moderation_status === 3) {
      return <span className="chip chip-exclude">Rejected: {c.moderation_reason || "Violation"}</span>;
    }
    return <span className="chip" style={{ background: "var(--color-warning-subtle)", color: "var(--color-warning-text)" }}>Pending Review</span>;
  };

  return (
    <div>
      <Card style={{ marginBottom: "var(--space-6)" }}>
        <div style={{ marginBottom: "var(--space-4)" }}>
          <h2 style={{ fontSize: "var(--font-size-xl)", fontWeight: "var(--font-weight-bold)", margin: "0 0 var(--space-1) 0" }}>
            Step 3: Ads & Creatives
          </h2>
          <p className="text-muted" style={{ margin: 0 }}>
            Create visual and interactive ad formats with fixed predefined dimensions. Each ad is strictly linked to an Ad Set and subject to automated moderation.
          </p>
        </div>

        {error && (
          <Alert variant="error" style={{ marginBottom: "var(--space-4)" }}>
            {error}
          </Alert>
        )}

        {/* Existing Ads Overview */}
        {creatives.length > 0 && (
          <div style={{ marginBottom: "var(--space-6)" }}>
            <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-3) 0" }}>
              Created Ads ({creatives.length})
            </h3>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "var(--space-4)" }}>
              {creatives.map((c) => {
                const adSet = adSets.find((a) => a.id === c.ad_set_id);
                const formatLabel =
                  CREATIVE_FORMAT_LABELS[c.creative_type] ||
                  (c.format ? c.format.replace("_", " ") : `${c.width}×${c.height}`);
                return (
                  <div
                    key={c.id}
                    style={{
                      border: "1px solid var(--color-border)",
                      borderRadius: "var(--radius-lg)",
                      padding: "var(--space-3)",
                      background: "var(--color-bg-surface)",
                      display: "flex",
                      flexDirection: "column",
                      gap: "var(--space-2)",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                      <strong style={{ fontSize: "var(--font-size-sm)" }}>{c.title}</strong>
                      <span className="text-muted" style={{ fontSize: "var(--font-size-xs)" }}>
                        {c.width}×{c.height}
                      </span>
                    </div>

                    <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>
                      Ad Set: <strong>{adSet?.name || "Default"}</strong> · {formatLabel}
                    </div>

                    {c.image_url && (
                      <img
                        src={c.image_url}
                        alt={c.title}
                        style={{ width: "100%", height: "120px", objectFit: "cover", borderRadius: "var(--radius-md)", background: "var(--color-bg-subtle)" }}
                        onError={(e) => {
                          (e.target as HTMLImageElement).src = "https://placehold.co/300x120.png?text=Creative+Asset";
                        }}
                      />
                    )}

                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "auto", paddingTop: "var(--space-2)" }}>
                      <div>{formatModerationBadge(c)}</div>
                      <a
                        href={c.landing_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ fontSize: "var(--font-size-xs)", color: "var(--color-primary)" }}
                      >
                        Visit Link ↗
                      </a>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Ad Creation and Live Preview Layout */}
        <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: "var(--space-6)", alignItems: "start" }}>
          {/* Ad Creation Form */}
          <div style={{ background: "var(--color-bg-subtle)", padding: "var(--space-4)", borderRadius: "var(--radius-lg)" }}>
            <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-3) 0" }}>
              Add Creative to Ad Set
            </h3>

            <form onSubmit={handleCreateAd}>
              <div style={{ marginBottom: "var(--space-3)" }}>
                <Select
                  label="Target Ad Set *"
                  value={String(activeAdSetId)}
                  onChange={(e) => setActiveAdSetId(Number(e.target.value))}
                  options={adSets.map((a) => ({
                    value: String(a.id),
                    label: `${a.name} (Sub-budget: $${a.daily_budget != null ? a.daily_budget : campaign.budget_daily})`,
                  }))}
                  required
                />
              </div>

              {/* Main Ad Category Dropdown */}
              <div style={{ marginBottom: "var(--space-3)" }}>
                <Select
                  label="Select Ad Category *"
                  value={category}
                  onChange={(e) => handleCategoryChange(e.target.value)}
                  options={[
                    { value: "", label: "-- Select Ad Category --" },
                    { value: "ai_chatbot", label: "AI and Chatbot Ads" },
                    { value: "banner", label: "Banner Ads" },
                  ]}
                  required
                />
              </div>

              {/* Conditional AI and Chatbot Format Dropdown */}
              {category === "ai_chatbot" && (
                <div style={{ marginBottom: "var(--space-3)" }}>
                  <Select
                    label="Select AI and Chatbot Ad Format *"
                    value={subcategory}
                    onChange={(e) => handleSubcategoryChange(e.target.value)}
                    options={[
                      { value: "", label: "-- Select AI and Chatbot Ad Format --" },
                      { value: "display_card", label: "Display Card (520 × 120)" },
                      { value: "label_ad", label: "Label Ads (400 × 100)" },
                      { value: "element_ad", label: "Element Ads (310 × 150)" },
                    ]}
                    required
                  />
                </div>
              )}

              {/* Selected Format & Fixed Dimensions Enforced Badge */}
              <div style={{ marginBottom: "var(--space-3)" }}>
                {activeFormat ? (
                  <div className="format-locked-badge">
                    <div>
                      <div style={{ fontSize: "11px", color: "var(--color-text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>
                        Enforced Format
                      </div>
                      <div style={{ fontWeight: 600, fontSize: "14px", marginTop: "2px", color: "var(--color-text-main)" }}>
                        {activeFormat.badge}
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
                      <span className="chip chip-include" style={{ fontSize: "11px" }}>
                        Fixed {activeFormat.width} × {activeFormat.height} px
                      </span>
                      <span className="text-muted" style={{ fontSize: "11px" }} title="Manual dimension edits are disabled">
                        🔒 Fixed
                      </span>
                    </div>
                  </div>
                ) : (
                  <div style={{ padding: "10px", background: "var(--color-bg-surface)", border: "1px dashed var(--color-border)", borderRadius: "var(--radius-md)", fontSize: "12px", color: "var(--color-text-muted)" }}>
                    {category === "ai_chatbot"
                      ? "Select an AI and Chatbot format above to assign predefined dimensions."
                      : "Select an ad category above to assign predefined dimensions."}
                  </div>
                )}
              </div>

              <div style={{ marginBottom: "var(--space-3)" }}>
                <Input
                  label="Creative Headline / Title *"
                  placeholder="e.g. Accelerate Your Software Deployments"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  required
                />
              </div>

              <div style={{ marginBottom: "var(--space-3)" }}>
                <Input
                  label="Creative Description (Optional)"
                  placeholder="Compelling promotional copy or disclosure"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>

              <div style={{ marginBottom: "var(--space-3)" }}>
                <Input
                  label="Asset Image URL (HTTPS)"
                  placeholder="https://..."
                  value={imageUrl}
                  onChange={(e) => setImageUrl(e.target.value)}
                  hint="Public HTTPS URL pointing to the creative image"
                />
              </div>

              <div style={{ marginBottom: "var(--space-4)" }}>
                <Input
                  label="HTTPS Landing URL *"
                  placeholder="https://yourbrand.com/landing"
                  value={landingUrl}
                  onChange={(e) => setLandingUrl(e.target.value)}
                  required
                />
                {domainStatus.host && (
                  <div style={{ fontSize: "var(--font-size-xs)", marginTop: "var(--space-1)", color: domainStatus.verified ? "var(--color-success-text)" : "var(--color-warning-text)" }}>
                    {domainStatus.verified
                      ? `✓ Domain ${domainStatus.host} is verified for this account`
                      : `⚠ Domain ${domainStatus.host} is not verified. Ads can be created, but activation requires verified domains.`}
                  </div>
                )}
              </div>

              <Button
                type="submit"
                variant="primary"
                isLoading={loading}
                disabled={loading || !title.trim() || !category || (category === "ai_chatbot" && !subcategory)}
              >
                + Save Ad to Selected Ad Set
              </Button>
            </form>
          </div>

          {/* Live Ad Preview Box */}
          <div>
            <h3 style={{ fontSize: "var(--font-size-base)", fontWeight: "var(--font-weight-semibold)", margin: "0 0 var(--space-3) 0" }}>
              Live Creative Preview
            </h3>
            <div className="ad-preview-box">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--space-2)" }}>
                <span className="badge badge-subtle" style={{ fontSize: "10px", textTransform: "uppercase" }}>
                  Sponsored
                </span>
                <span className="text-muted" style={{ fontSize: "11px", fontWeight: "var(--font-weight-medium)" }}>
                  {activeFormat ? `${activeFormat.name} (${activeFormat.width} × ${activeFormat.height})` : "No Format Selected"}
                </span>
              </div>

              {activeFormatKey === "display_card" && (
                /* Display Card: Wide horizontal layout (520 × 120 px) */
                <div className="preview-format-container preview-layout-display-card">
                  {imageUrl ? (
                    <img
                      src={imageUrl}
                      alt="Asset Preview"
                      className="format-thumb"
                      onError={(e) => {
                        (e.target as HTMLImageElement).src = "https://placehold.co/520x120.png?text=Display+Card";
                      }}
                    />
                  ) : (
                    <div className="format-thumb" style={{ display: "flex", alignItems: "center", justifyContent: "center", background: "var(--color-bg-subtle)", color: "var(--color-text-muted)", fontSize: "10px" }}>
                      520 × 120
                    </div>
                  )}
                  <div className="format-content">
                    <div>
                      <div style={{ fontSize: "10px", color: "var(--color-text-muted)", textTransform: "uppercase", marginBottom: "2px" }}>
                        AI Assistant Card · {domainStatus.host || "example.com"}
                      </div>
                      <div style={{ fontWeight: 600, fontSize: "13px", lineHeight: 1.3, color: "var(--color-text-main)" }}>
                        {title || "Your Compelling Headline Here"}
                      </div>
                      <p className="text-muted" style={{ fontSize: "11px", margin: "2px 0 0 0", overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                        {description || "Description preview text formatted for conversational card context."}
                      </p>
                    </div>
                    <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center" }}>
                      <span style={{ fontSize: "11px", color: "var(--color-primary)", fontWeight: 500 }}>
                        Learn More →
                      </span>
                    </div>
                  </div>
                </div>
              )}

              {activeFormatKey === "label_ad" && (
                /* Label Ads: Compact horizontal layout (400 × 100 px) */
                <div className="preview-format-container preview-layout-label-ad">
                  {imageUrl ? (
                    <img
                      src={imageUrl}
                      alt="Asset Preview"
                      className="format-thumb"
                      onError={(e) => {
                        (e.target as HTMLImageElement).src = "https://placehold.co/400x100.png?text=Label+Ad";
                      }}
                    />
                  ) : (
                    <div className="format-thumb" style={{ display: "flex", alignItems: "center", justifyContent: "center", background: "var(--color-bg-subtle)", color: "var(--color-text-muted)", fontSize: "10px" }}>
                      400 × 100
                    </div>
                  )}
                  <div className="format-content">
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <span style={{ fontSize: "10px", color: "var(--color-text-muted)" }}>
                        {domainStatus.host || "example.com"}
                      </span>
                      <span style={{ fontSize: "10px", color: "var(--color-primary)", fontWeight: 600 }}>
                        Visit ↗
                      </span>
                    </div>
                    <div style={{ fontWeight: 600, fontSize: "12px", lineHeight: 1.2, color: "var(--color-text-main)" }}>
                      {title || "Compact Sponsored Label Ad Headline"}
                    </div>
                    <div className="text-muted" style={{ fontSize: "10px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {description || "Subtext for compact conversational suggestion label."}
                    </div>
                  </div>
                </div>
              )}

              {activeFormatKey === "element_ad" && (
                /* Element Ads: Medium rectangular layout (310 × 150 px) */
                <div className="preview-format-container preview-layout-element-ad">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontSize: "10px", color: "var(--color-text-muted)" }}>
                      Assistant Element · {domainStatus.host || "example.com"}
                    </span>
                    <span style={{ fontSize: "10px", color: "var(--color-primary)", fontWeight: 500 }}>
                      Open →
                    </span>
                  </div>
                  {imageUrl && (
                    <img
                      src={imageUrl}
                      alt="Asset Preview"
                      className="format-thumb"
                      onError={(e) => {
                        (e.target as HTMLImageElement).src = "https://placehold.co/310x150.png?text=Element+Ad";
                      }}
                    />
                  )}
                  <div className="format-content">
                    <div style={{ fontWeight: 600, fontSize: "12px", lineHeight: 1.3, color: "var(--color-text-main)" }}>
                      {title || "Chatbot Assistant Interactive Element"}
                    </div>
                    <p className="text-muted" style={{ fontSize: "11px", margin: 0, overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                      {description || "Rich interactive element preview embedded directly into chat responses."}
                    </p>
                  </div>
                </div>
              )}

              {activeFormatKey === "banner" && (
                /* Banner Ads: Square layout (500 × 500 px) */
                <div className="preview-format-container preview-layout-banner">
                  {imageUrl ? (
                    <img
                      src={imageUrl}
                      alt="Asset Preview"
                      className="format-thumb"
                      onError={(e) => {
                        (e.target as HTMLImageElement).src = "https://placehold.co/500x500.png?text=Banner+Ad";
                      }}
                    />
                  ) : (
                    <div className="format-thumb" style={{ display: "flex", alignItems: "center", justifyContent: "center", background: "var(--color-bg-subtle)", color: "var(--color-text-muted)" }}>
                      500 × 500 px
                    </div>
                  )}
                  <div className="format-content">
                    <div style={{ fontWeight: 700, fontSize: "14px", lineHeight: 1.3 }}>
                      {title || "High-Impact Square Banner Headline"}
                    </div>
                    <p className="text-muted" style={{ fontSize: "12px", margin: 0 }}>
                      {description || "Full visual banner presentation tailored for high-conversion web display placements."}
                    </p>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderTop: "1px solid var(--color-border)", paddingTop: "var(--space-2)", marginTop: "var(--space-1)" }}>
                      <span style={{ fontSize: "11px", color: "var(--color-text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "200px" }}>
                        {domainStatus.host || "example.com"}
                      </span>
                      <span style={{ fontSize: "11px", color: "var(--color-primary)", fontWeight: 600 }}>
                        Learn More →
                      </span>
                    </div>
                  </div>
                </div>
              )}

              {!activeFormat && (
                /* Placeholder before format selection */
                <div
                  style={{
                    border: "1px dashed var(--color-border)",
                    borderRadius: "var(--radius-md)",
                    padding: "var(--space-6) var(--space-4)",
                    textAlign: "center",
                    color: "var(--color-text-muted)",
                    margin: "var(--space-3) 0",
                  }}
                >
                  <div style={{ fontSize: "28px", marginBottom: "var(--space-2)" }}>📐</div>
                  <div style={{ fontWeight: 500, fontSize: "13px", marginBottom: "4px" }}>
                    No Format Selected
                  </div>
                  <div style={{ fontSize: "11px" }}>
                    Select an Ad Category above to activate the live creative preview.
                  </div>
                </div>
              )}

              {/* Footer summary of preview */}
              {activeFormat && (
                <div style={{ fontSize: "11px", color: "var(--color-text-muted)", display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "var(--space-2)" }}>
                  <span>Preview Format: <strong>{activeFormat.name}</strong></span>
                  <span>Dimensions: <strong>{activeFormat.width} × {activeFormat.height} px</strong></span>
                </div>
              )}
            </div>

            <div style={{ marginTop: "var(--space-4)" }}>
              <Alert variant="info" title="Moderation & Eligibility Notice">
                Newly created ads undergo server-side moderation. If inappropriate content or unverified landing domains are detected, ads remain PENDING or REJECTED and will not serve until resolved.
              </Alert>
            </div>
          </div>
        </div>

        {/* Wizard Footer Controls */}
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: "var(--space-6)", paddingTop: "var(--space-4)", borderTop: "1px solid var(--color-border)" }}>
          <Button variant="outline" onClick={onBack}>
            ← Back to Ad Sets
          </Button>
          <Button
            variant="primary"
            onClick={onContinue}
            disabled={creatives.length === 0}
          >
            Continue to Review & Launch →
          </Button>
        </div>
      </Card>
    </div>
  );
}
