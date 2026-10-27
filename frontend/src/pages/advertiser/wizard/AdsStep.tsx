import { useState, useEffect, type FormEvent } from "react";
import {
  createCreative,
  listCreatives,
  listAdvertiserDomains,
  type AdvertiserDomain,
} from "../../../api/client";
import { CREATIVE_TYPES, type Campaign, type AdSet, type Creative } from "../../../types";
import { Button, Input, Select, Alert, Card, StatusBadge } from "../../../components/ui";

interface AdsStepProps {
  campaign: Campaign;
  adSets: AdSet[];
  creatives: Creative[];
  selectedAdSetId: number | null;
  onCreativesUpdated: (creatives: Creative[]) => void;
  onBack: () => void;
  onContinue: () => void;
}

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

  // Form state
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [creativeType, setCreativeType] = useState("1"); // 1=banner
  const [imageUrl, setImageUrl] = useState("https://placehold.co/300x250.png");
  const [videoUrl, setVideoUrl] = useState("");
  const [landingUrl, setLandingUrl] = useState("https://example.com");
  const [width, setWidth] = useState("300");
  const [height, setHeight] = useState("250");

  useEffect(() => {
    void listAdvertiserDomains().then(setDomains).catch(() => {});
  }, []);

  // Standard creative sizes helper
  const handleSizePreset = (w: string, h: string) => {
    setWidth(w);
    setHeight(h);
    if (imageUrl.includes("placehold.co")) {
      setImageUrl(`https://placehold.co/${w}x${h}.png`);
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
        creative_type: Number(creativeType),
        image_url: imageUrl.trim() || undefined,
        video_url: videoUrl.trim() || undefined,
        landing_url: landingUrl.trim(),
        width: Number(width) || 300,
        height: Number(height) || 250,
      });

      const updatedCreatives = [...creatives, newCreative];
      onCreativesUpdated(updatedCreatives);

      // Reset form fields
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
            Create visual and interactive ad formats. Each ad is strictly linked to an Ad Set and subject to automated moderation.
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
                        {c.width}x{c.height}
                      </span>
                    </div>

                    <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>
                      Ad Set: <strong>{adSet?.name || "Default"}</strong> | {CREATIVE_TYPES[c.creative_type] || "Banner"}
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

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
                <Select
                  label="Format Type"
                  value={creativeType}
                  onChange={(e) => setCreativeType(e.target.value)}
                  options={Object.entries(CREATIVE_TYPES).map(([v, l]) => ({ value: v, label: l }))}
                />
                <div>
                  <label className="form-label">Common Dimensions</label>
                  <div style={{ display: "flex", gap: "var(--space-1)", marginTop: "var(--space-1)" }}>
                    <Button type="button" variant="outline" size="sm" onClick={() => handleSizePreset("300", "250")}>
                      300x250
                    </Button>
                    <Button type="button" variant="outline" size="sm" onClick={() => handleSizePreset("728", "90")}>
                      728x90
                    </Button>
                    <Button type="button" variant="outline" size="sm" onClick={() => handleSizePreset("320", "50")}>
                      320x50
                    </Button>
                  </div>
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
                <Input
                  label="Width (px)"
                  type="number"
                  min="0"
                  value={width}
                  onChange={(e) => setWidth(e.target.value)}
                />
                <Input
                  label="Height (px)"
                  type="number"
                  min="0"
                  value={height}
                  onChange={(e) => setHeight(e.target.value)}
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

              <Button type="submit" variant="primary" isLoading={loading} disabled={loading || !title.trim()}>
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
                <span className="text-muted" style={{ fontSize: "11px" }}>
                  {width} × {height}
                </span>
              </div>

              {imageUrl ? (
                <img
                  src={imageUrl}
                  alt="Live Preview"
                  className="ad-preview-thumb"
                  onError={(e) => {
                    (e.target as HTMLImageElement).src = "https://placehold.co/300x180.png?text=Asset+Preview";
                  }}
                />
              ) : (
                <div
                  className="ad-preview-thumb"
                  style={{ display: "flex", alignItems: "center", justifyContent: "center", color: "var(--color-text-muted)" }}
                >
                  No Asset Selected
                </div>
              )}

              <div style={{ fontWeight: "var(--font-weight-bold)", fontSize: "var(--font-size-sm)", marginBottom: "var(--space-1)" }}>
                {title || "Your Compelling Headline Here"}
              </div>

              <p className="text-muted" style={{ fontSize: "var(--font-size-xs)", margin: "0 0 var(--space-3) 0" }}>
                {description || "Description preview text will display here to engage prospective customers."}
              </p>

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderTop: "1px solid var(--color-border)", paddingTop: "var(--space-2)" }}>
                <span style={{ fontSize: "11px", color: "var(--color-text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "180px" }}>
                  {domainStatus.host || "example.com"}
                </span>
                <span style={{ fontSize: "11px", color: "var(--color-primary)", fontWeight: "var(--font-weight-medium)" }}>
                  Learn More →
                </span>
              </div>
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
