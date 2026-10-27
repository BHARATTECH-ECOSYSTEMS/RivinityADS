import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { getCampaign, listAdSets, listCreatives } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import type { Campaign, AdSet, Creative } from "../../types";
import { PageContainer, PageHeader, LoadingSkeleton, Alert } from "../../components/ui";
import { CampaignStep } from "./wizard/CampaignStep";
import { AdSetsStep } from "./wizard/AdSetsStep";
import { AdsStep } from "./wizard/AdsStep";
import { ReviewStep } from "./wizard/ReviewStep";
import type { WizardStep } from "./wizard/types";

export default function NewCampaign() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const { session } = useAuth();

  const campaignIdParam = searchParams.get("campaign_id");
  const stepParam = searchParams.get("step");
  const adSetIdParam = searchParams.get("ad_set_id");

  const [step, setStep] = useState<WizardStep>(() => {
    const s = Number(stepParam);
    return s >= 1 && s <= 4 ? (s as WizardStep) : 1;
  });

  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [adSets, setAdSets] = useState<AdSet[]>([]);
  const [selectedAdSetId, setSelectedAdSetId] = useState<number | null>(
    adSetIdParam ? Number(adSetIdParam) : null
  );
  const [creatives, setCreatives] = useState<Creative[]>([]);
  const [initialLoading, setInitialLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Sync step to URL query param
  const goToStep = (nextStep: WizardStep, newCampaignId?: number, newAdSetId?: number) => {
    setStep(nextStep);
    const params = new URLSearchParams(searchParams);
    params.set("step", String(nextStep));
    const cid = newCampaignId || campaign?.id;
    if (cid) params.set("campaign_id", String(cid));
    const asid = newAdSetId || selectedAdSetId;
    if (asid) params.set("ad_set_id", String(asid));
    setSearchParams(params, { replace: true });
  };

  // Load existing campaign if campaign_id is present in URL (e.g. on page refresh or resume)
  useEffect(() => {
    if (!campaignIdParam) return;
    const cid = Number(campaignIdParam);
    if (isNaN(cid) || cid <= 0) return;

    let isMounted = true;
    setInitialLoading(true);
    setLoadError(null);

    Promise.all([
      getCampaign(cid),
      listAdSets(cid).catch(() => []),
      listCreatives(cid).catch(() => []),
    ])
      .then(([campRes, adSetsRes, creativesRes]) => {
        if (!isMounted) return;
        setCampaign(campRes);
        setAdSets(adSetsRes);
        setCreatives(creativesRes);
        if (adSetsRes.length > 0 && !selectedAdSetId) {
          setSelectedAdSetId(adSetsRes[0].id);
        }
      })
      .catch((err) => {
        if (!isMounted) return;
        setLoadError(err instanceof Error ? err.message : "Failed to restore campaign draft");
      })
      .finally(() => {
        if (isMounted) setInitialLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, [campaignIdParam]);

  const handleCampaignSaved = (saved: Campaign) => {
    setCampaign(saved);
  };

  const handleAdSetsUpdated = (updatedAdSets: AdSet[], newSelectedId?: number) => {
    setAdSets(updatedAdSets);
    if (newSelectedId) {
      setSelectedAdSetId(newSelectedId);
      const params = new URLSearchParams(searchParams);
      params.set("ad_set_id", String(newSelectedId));
      setSearchParams(params, { replace: true });
    }
  };

  const handleCreativesUpdated = (updatedCreatives: Creative[]) => {
    setCreatives(updatedCreatives);
  };

  const handleCampaignActivated = (activatedCampaign: Campaign) => {
    setCampaign(activatedCampaign);
  };

  const STEPS: { id: WizardStep; label: string; desc: string }[] = [
    { id: 1, label: "Campaign", desc: "Budgets & Strategy" },
    { id: 2, label: "Ad Sets", desc: "Targeting & Bids" },
    { id: 3, label: "Ads", desc: "Creatives & Formats" },
    { id: 4, label: "Review", desc: "Launch Verification" },
  ];

  return (
    <PageContainer>
      <PageHeader
        title={campaign ? `Campaign Builder: ${campaign.name}` : "Create Advertising Campaign"}
        description="Structured 3-step hierarchy: Campaign master budgets → Ad Set audiences → Individual Creative ads."
      />

      {/* Stepper Navigation */}
      <nav className="stepper-nav" aria-label="Campaign creation workflow">
        {STEPS.map((s, index) => {
          const isActive = step === s.id;
          const isCompleted = step > s.id;
          const canNavigate = s.id === 1 || (campaign != null && s.id <= step + 1);

          return (
            <div key={s.id} style={{ display: "flex", alignItems: "center", flex: index < STEPS.length - 1 ? 1 : undefined }}>
              <button
                type="button"
                className={`stepper-item ${isActive ? "is-active" : ""} ${isCompleted ? "is-completed" : ""}`}
                onClick={() => {
                  if (canNavigate) goToStep(s.id);
                }}
                disabled={!canNavigate}
                aria-current={isActive ? "step" : undefined}
              >
                <div className="stepper-indicator">
                  {isCompleted ? "✓" : s.id}
                </div>
                <div>
                  <div style={{ lineHeight: 1.2 }}>{s.label}</div>
                  <div className="text-muted" style={{ fontSize: "11px", fontWeight: "normal" }}>
                    {s.desc}
                  </div>
                </div>
              </button>

              {index < STEPS.length - 1 && (
                <div className={`stepper-divider ${step > s.id ? "is-completed" : ""}`} />
              )}
            </div>
          );
        })}
      </nav>

      {loadError && (
        <Alert variant="error" style={{ marginBottom: "var(--space-4)" }}>
          {loadError}
        </Alert>
      )}

      {initialLoading ? (
        <LoadingSkeleton variant="rect" height={220} />
      ) : (
        <>
          {step === 1 && (
            <CampaignStep
              campaign={campaign}
              onSaved={handleCampaignSaved}
              onContinue={() => goToStep(2)}
            />
          )}

          {step === 2 && campaign && (
            <AdSetsStep
              campaign={campaign}
              adSets={adSets}
              selectedAdSetId={selectedAdSetId}
              onAdSetsUpdated={handleAdSetsUpdated}
              onBack={() => goToStep(1)}
              onContinue={() => goToStep(3)}
            />
          )}

          {step === 3 && campaign && (
            <AdsStep
              campaign={campaign}
              adSets={adSets}
              creatives={creatives}
              selectedAdSetId={selectedAdSetId}
              onCreativesUpdated={handleCreativesUpdated}
              onBack={() => goToStep(2)}
              onContinue={() => goToStep(4)}
            />
          )}

          {step === 4 && campaign && (
            <ReviewStep
              campaign={campaign}
              adSets={adSets}
              creatives={creatives}
              onBack={() => goToStep(3)}
              onActivated={handleCampaignActivated}
            />
          )}
        </>
      )}
    </PageContainer>
  );
}
