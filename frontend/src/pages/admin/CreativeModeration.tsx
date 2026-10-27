import React, { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  approveCreative,
  listAdvertisers,
  listCampaigns,
  listPendingCreatives,
  rejectCreative,
  reopenCreative,
  type ModerationCreative,
} from "../../api/client";
import type { Advertiser, Campaign } from "../../types";
import {
  PageContainer,
  PageHeader,
  Card,
  Button,
  StatusBadge,
  Alert,
  LoadingSkeleton,
  EmptyState,
  ConfirmDialog,
} from "../../components/ui";

const MODERATION_LABEL: Record<ModerationCreative["moderation_status"], string> = {
  1: "Pending Review",
  2: "Approved",
  3: "Rejected",
};

const CREATIVE_TYPE_LABEL: Record<number, string> = {
  1: "Banner",
  2: "Native",
  3: "Video",
  4: "Interstitial",
};

function messageFor(error: unknown, fallback: string) {
  if (error instanceof ApiError && error.status === 403) {
    return "You are not authorized to moderate creatives.";
  }
  return error instanceof Error ? error.message : fallback;
}

export default function CreativeModeration() {
  const [pending, setPending] = useState<ModerationCreative[]>([]);
  const [decisions, setDecisions] = useState<Record<number, ModerationCreative>>({});
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [advertisers, setAdvertisers] = useState<Advertiser[]>([]);
  const [loading, setLoading] = useState(true);

  // In-flight action tracking
  const [actionId, setActionId] = useState<number | null>(null);
  const [reasonFor, setReasonFor] = useState<ModerationCreative | null>(null);
  const [reason, setReason] = useState("");
  const [rejectError, setRejectError] = useState<string | null>(null);

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Filter tabs
  const [filterTab, setFilterTab] = useState<"pending" | "all">("pending");

  const loadQueue = async () => {
    setLoading(true);
    setError(null);
    try {
      const [creativeRows, campaignRows, advertiserRows] = await Promise.all([
        listPendingCreatives(),
        listCampaigns(),
        listAdvertisers(),
      ]);
      setPending(creativeRows);
      setCampaigns(campaignRows);
      setAdvertisers(advertiserRows);
    } catch (err) {
      setError(messageFor(err, "Could not load the moderation queue."));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadQueue();
  }, []);

  const campaignById = useMemo(() => new Map(campaigns.map((c) => [c.id, c])), [campaigns]);
  const advertiserById = useMemo(() => new Map(advertisers.map((a) => [a.id, a])), [advertisers]);

  const allCreatives = useMemo(() => {
    const list = [...pending];
    for (const dec of Object.values(decisions)) {
      if (!list.some((item) => item.id === dec.id)) {
        list.push(dec);
      }
    }
    return list;
  }, [pending, decisions]);

  const displayedCreatives = filterTab === "pending"
    ? allCreatives.filter((c) => c.moderation_status === 1)
    : allCreatives;

  const updateDecision = (creative: ModerationCreative, action: "approve" | "reject" | "reopen") => {
    setActionId(creative.id);
    setError(null);
    setNotice(null);

    const request =
      action === "approve"
        ? approveCreative(creative.id)
        : action === "reopen"
        ? reopenCreative(creative.id)
        : rejectCreative(creative.id, reason.trim());

    request
      .then((updated) => {
        setDecisions((current) => ({ ...current, [updated.id]: updated }));
        setPending((current) => current.filter((item) => item.id !== updated.id));
        setReason("");
        setReasonFor(null);
        setNotice(`Creative "${updated.title}" is now ${MODERATION_LABEL[updated.moderation_status].toLowerCase()}.`);
        return loadQueue();
      })
      .catch((err: unknown) => setError(messageFor(err, "Moderation action failed.")))
      .finally(() => setActionId(null));
  };

  const submitReject = () => {
    if (!reason.trim()) {
      setRejectError("A valid rejection reason is mandatory.");
      return;
    }
    if (!reasonFor) return;
    setRejectError(null);
    updateDecision(reasonFor, "reject");
  };

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Platform Compliance"
        title="Creative Moderation"
        description="Inspect advertiser creative assets, verify landing destinations, and approve or reject submissions before serving."
        actions={
          <Button variant="secondary" size="sm" onClick={loadQueue} isLoading={loading}>
            Refresh Queue
          </Button>
        }
      />

      {error && (
        <Alert variant="danger" title="Moderation Error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {notice && (
        <Alert variant="success" onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      )}

      {/* Filter Tabs */}
      <div style={{ display: "flex", gap: "8px", marginBottom: "20px" }}>
        <button
          type="button"
          className={`btn ${filterTab === "pending" ? "btn-primary" : "btn-secondary"} btn-sm`}
          onClick={() => setFilterTab("pending")}
        >
          Pending Review ({pending.length})
        </button>
        <button
          type="button"
          className={`btn ${filterTab === "all" ? "btn-primary" : "btn-secondary"} btn-sm`}
          onClick={() => setFilterTab("all")}
        >
          All Reviewed ({allCreatives.length})
        </button>
      </div>

      {loading ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <LoadingSkeleton variant="rect" height={160} />
          <LoadingSkeleton variant="rect" height={160} />
        </div>
      ) : displayedCreatives.length === 0 ? (
        <Card>
          <EmptyState
            title="Moderation queue is empty"
            description={
              filterTab === "pending"
                ? "No creative assets are currently awaiting review. All submissions have been processed."
                : "No creative submissions recorded in this session."
            }
          />
        </Card>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
          {displayedCreatives.map((creative) => {
            const campaign = campaignById.get(creative.campaign_id);
            const advertiser = campaign ? advertiserById.get(campaign.advertiser_id) : undefined;
            const isBusy = actionId === creative.id;
            const isPending = creative.moderation_status === 1;
            const isRejected = creative.moderation_status === 3;
            const isApproved = creative.moderation_status === 2;

            return (
              <Card
                key={creative.id}
                title={creative.title}
                subtitle={`Creative #${creative.id} · Type: ${CREATIVE_TYPE_LABEL[creative.creative_type] || `Type #${creative.creative_type}`} · Dimensions: ${creative.width}×${creative.height}px`}
                footer={
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
                    <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                      Submitted {new Date(creative.created_at).toLocaleString()}
                    </div>
                    <div style={{ display: "flex", gap: "8px" }}>
                      {isPending && (
                        <>
                          <Button
                            variant="primary"
                            size="sm"
                            onClick={() => updateDecision(creative, "approve")}
                            isLoading={isBusy}
                          >
                            Approve
                          </Button>
                          <Button
                            variant="danger"
                            size="sm"
                            onClick={() => {
                              setReasonFor(creative);
                              setReason("");
                              setRejectError(null);
                            }}
                            disabled={isBusy}
                          >
                            Reject...
                          </Button>
                        </>
                      )}
                      {isRejected && (
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => updateDecision(creative, "reopen")}
                          isLoading={isBusy}
                        >
                          Reopen for Review
                        </Button>
                      )}
                    </div>
                  </div>
                }
              >
                <div style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: "24px", alignItems: "start" }}>
                  
                  {/* Creative Asset Preview Box */}
                  <div
                    style={{
                      border: "1px solid var(--border-color)",
                      borderRadius: "var(--radius-lg)",
                      overflow: "hidden",
                      backgroundColor: "var(--color-bg-subtle)",
                      minHeight: "180px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      position: "relative",
                    }}
                  >
                    {creative.image_url ? (
                      <img
                        src={creative.image_url}
                        alt={creative.title}
                        style={{ maxWidth: "100%", maxHeight: "240px", objectFit: "contain", display: "block" }}
                        onError={(e) => {
                          // Fallback display if asset URL fails to load
                          (e.target as HTMLElement).style.display = "none";
                        }}
                      />
                    ) : creative.video_url ? (
                      <video
                        src={creative.video_url}
                        controls
                        style={{ maxWidth: "100%", maxHeight: "240px" }}
                      />
                    ) : (
                      <div style={{ padding: "24px", textAlign: "center", color: "var(--color-text-muted)", fontSize: "0.875rem" }}>
                        <div>🖼️ Asset Preview</div>
                        <div style={{ fontSize: "0.75rem", marginTop: "4px" }}>
                          No image or video URL provided
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Metadata and Moderation Info */}
                  <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                    <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                      <StatusBadge
                        status={isApproved ? "success" : isRejected ? "failed" : "pending"}
                        label={MODERATION_LABEL[creative.moderation_status]}
                        size="sm"
                      />
                      <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                        Quality Score: <strong>{creative.quality_score}</strong>
                      </span>
                    </div>

                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", fontSize: "0.8125rem", marginTop: "4px" }}>
                      <div>
                        <span style={{ color: "var(--color-text-muted)" }}>Advertiser: </span>
                        <strong>{advertiser?.name || (campaign ? `Advertiser #${campaign.advertiser_id}` : "Unavailable")}</strong>
                      </div>
                      <div>
                        <span style={{ color: "var(--color-text-muted)" }}>Campaign: </span>
                        <strong>{campaign?.name || `Campaign #${creative.campaign_id}`}</strong>
                      </div>
                    </div>

                    {creative.description && (
                      <div style={{ fontSize: "0.875rem", color: "var(--color-text-secondary)", marginTop: "4px" }}>
                        {creative.description}
                      </div>
                    )}

                    <div style={{ fontSize: "0.8125rem", marginTop: "6px" }}>
                      <span style={{ color: "var(--color-text-muted)" }}>Destination Landing URL: </span>
                      <a
                        href={creative.landing_url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ color: "var(--color-primary)", wordBreak: "break-all", fontWeight: 500 }}
                      >
                        {creative.landing_url} ↗
                      </a>
                    </div>

                    {creative.moderation_reason && (
                      <div
                        style={{
                          marginTop: "8px",
                          padding: "10px 14px",
                          borderRadius: "var(--radius-md)",
                          backgroundColor: "#fef2f2",
                          border: "1px solid #fecaca",
                          color: "#991b1b",
                          fontSize: "0.8125rem",
                        }}
                      >
                        <strong>Rejection Reason:</strong> {creative.moderation_reason}
                      </div>
                    )}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {/* Rejection Reason Modal */}
      {reasonFor && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(15, 23, 42, 0.6)",
            backdropFilter: "blur(2px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: "20px",
          }}
        >
          <div
            style={{
              backgroundColor: "var(--color-bg-surface)",
              borderRadius: "var(--radius-xl)",
              maxWidth: "520px",
              width: "100%",
              padding: "24px",
              boxShadow: "var(--shadow-xl)",
              border: "1px solid var(--border-color)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
              <h3 style={{ margin: 0, fontSize: "1.125rem", fontWeight: 700, color: "var(--color-danger)" }}>
                Reject Creative: {reasonFor.title}
              </h3>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setReasonFor(null)}
                aria-label="Close dialog"
              >
                ✕
              </button>
            </div>

            <p style={{ fontSize: "0.875rem", color: "var(--color-text-secondary)", marginBottom: "16px" }}>
              Please specify the compliance or policy reason for rejecting this creative asset. This reason will be transmitted to the advertiser.
            </p>

            {rejectError && (
              <Alert variant="danger" onClose={() => setRejectError(null)}>
                {rejectError}
              </Alert>
            )}

            <div className="field" style={{ marginBottom: "20px" }}>
              <label htmlFor="rejection-reason" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "6px", display: "block" }}>
                Rejection Reason *
              </label>
              <textarea
                id="rejection-reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={3}
                maxLength={2000}
                required
                className="input"
                placeholder="e.g. Inappropriate landing URL content, misleading claim, or non-conforming dimensions."
                style={{ width: "100%", resize: "vertical" }}
              />
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <Button variant="secondary" onClick={() => setReasonFor(null)} disabled={actionId === reasonFor.id}>
                Cancel
              </Button>
              <Button 
                variant="danger" 
                onClick={submitReject} 
                isLoading={actionId === reasonFor.id}
                disabled={!reason.trim()}
              >
                Confirm Rejection
              </Button>
            </div>
          </div>
        </div>
      )}
    </PageContainer>
  );
}
