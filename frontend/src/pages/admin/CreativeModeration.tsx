import { useEffect, useMemo, useState } from "react";
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

const MODERATION_LABEL: Record<ModerationCreative["moderation_status"], string> = {
  1: "Pending review",
  2: "Approved",
  3: "Rejected",
};
const CREATIVE_TYPE_LABEL: Record<number, string> = { 1: "Banner", 2: "Native", 3: "Video", 4: "Interstitial" };

function messageFor(error: unknown, fallback: string) {
  if (error instanceof ApiError && error.status === 403) return "You are not authorized to moderate creatives.";
  return error instanceof Error ? error.message : fallback;
}

export default function CreativeModeration() {
  const [pending, setPending] = useState<ModerationCreative[]>([]);
  const [decisions, setDecisions] = useState<Record<number, ModerationCreative>>({});
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [advertisers, setAdvertisers] = useState<Advertiser[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionId, setActionId] = useState<number | null>(null);
  const [reasonFor, setReasonFor] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

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

  useEffect(() => { void loadQueue(); }, []);

  const campaignById = useMemo(() => new Map(campaigns.map((campaign) => [campaign.id, campaign])), [campaigns]);
  const advertiserById = useMemo(() => new Map(advertisers.map((advertiser) => [advertiser.id, advertiser])), [advertisers]);
  const rows = [...pending, ...Object.values(decisions).filter((decision) => !pending.some((item) => item.id === decision.id))];

  const updateDecision = (creative: ModerationCreative, action: "approve" | "reject" | "reopen") => {
    setActionId(creative.id);
    setError(null);
    setNotice(null);
    const request = action === "approve" ? approveCreative(creative.id) : action === "reopen" ? reopenCreative(creative.id) : rejectCreative(creative.id, reason.trim());
    request
      .then((updated) => {
        setDecisions((current) => ({ ...current, [updated.id]: updated }));
        setPending((current) => current.filter((item) => item.id !== updated.id));
        setReason("");
        setReasonFor(null);
        setNotice(`${updated.title} is now ${MODERATION_LABEL[updated.moderation_status].toLowerCase()}.`);
        return loadQueue();
      })
      .catch((err: unknown) => setError(messageFor(err, "Moderation action failed.")))
      .finally(() => setActionId(null));
  };

  const submitReject = (creative: ModerationCreative) => {
    if (!reason.trim()) {
      setError("A rejection reason is required.");
      return;
    }
    updateDecision(creative, "reject");
  };

  return <div>
    <h1>Creative moderation</h1>
    <p className="muted">Review advertiser creatives before they can be activated or served.</p>
    {error && <div className="card"><p role="alert" className="error">{error}</p><button className="btn secondary small" onClick={() => void loadQueue()} disabled={loading}>Retry</button></div>}
    {notice && <p role="status" className="success">{notice}</p>}
    <div className="card">
      <h2>Review queue</h2>
      {loading ? <p role="status" className="muted">Loading creatives...</p> : rows.length === 0 ? <p className="muted">No creatives are awaiting review.</p> : <div className="moderation-list">{rows.map((creative) => {
        const campaign = campaignById.get(creative.campaign_id);
        const advertiser = campaign ? advertiserById.get(campaign.advertiser_id) : undefined;
        const busy = actionId === creative.id;
        const pendingReview = creative.moderation_status === 1;
        const rejecting = reasonFor === creative.id;
        return <article className="moderation-item" key={creative.id}>
          <div className="moderation-header"><div><h3>{creative.title}</h3><span className={`badge ${creative.moderation_status === 2 ? "active" : "paused"}`}>{MODERATION_LABEL[creative.moderation_status]}</span></div><span className="muted">Submitted {new Date(creative.created_at).toLocaleString()}</span></div>
          <dl className="moderation-details"><dt>Advertiser</dt><dd>{advertiser?.name ?? (campaign ? `Advertiser #${campaign.advertiser_id}` : "Unavailable")}</dd><dt>Campaign</dt><dd>{campaign?.name ?? `Campaign #${creative.campaign_id}`}</dd><dt>Creative type</dt><dd>{CREATIVE_TYPE_LABEL[creative.creative_type] ?? `Type ${creative.creative_type}`}</dd><dt>Destination</dt><dd><a href={creative.landing_url} target="_blank" rel="noreferrer">{creative.landing_url}</a></dd>{creative.description && <><dt>Description</dt><dd>{creative.description}</dd></>}{creative.moderation_reason && <><dt>Reason</dt><dd>{creative.moderation_reason}</dd></>}</dl>
          {pendingReview && <div className="actions"><button className="btn" onClick={() => updateDecision(creative, "approve")} disabled={busy}>{busy ? "Working..." : "Approve"}</button><button className="btn secondary" onClick={() => { setReasonFor(rejecting ? null : creative.id); setReason(""); setError(null); }} disabled={busy}>Reject</button></div>}
          {rejecting && pendingReview && <div className="moderation-reject"><label htmlFor={`reason-${creative.id}`}>Rejection reason *</label><textarea id={`reason-${creative.id}`} value={reason} onChange={(event) => setReason(event.target.value)} rows={3} maxLength={2000} required /><div className="actions"><button className="btn" onClick={() => submitReject(creative)} disabled={busy}>{busy ? "Rejecting..." : "Confirm rejection"}</button><button className="btn secondary" onClick={() => setReasonFor(null)} disabled={busy}>Cancel</button></div></div>}
          {creative.moderation_status === 3 && <button className="btn secondary small" onClick={() => updateDecision(creative, "reopen")} disabled={busy}>{busy ? "Working..." : "Reopen for review"}</button>}
        </article>;
      })}</div>}
    </div>
  </div>;
}
