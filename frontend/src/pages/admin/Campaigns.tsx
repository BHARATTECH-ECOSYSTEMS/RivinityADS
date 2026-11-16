import React, { useEffect, useState } from "react";
import { 
  listAdvertisers, 
  listCampaigns, 
  setCampaignStatus,
  getOptimizationStatus,
  listOptimizationDecisions,
  revertOptimizationDecision,
  type OptimizationStatus,
  type OptimizationDecisionRecord,
} from "../../api/client";
import { ctr, money, num } from "../../lib/format";
import { BID_TYPES, type Advertiser, type Campaign } from "../../types";
import CampaignAssignmentModal from "./CampaignAssignmentModal";
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

export default function CampaignsAdmin() {
  const [rows, setRows] = useState<Campaign[]>([]);
  const [advertisers, setAdvertisers] = useState<Advertiser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [assigningCampaign, setAssigningCampaign] = useState<Campaign | null>(null);

  // Optimization governance state
  const [optStatus, setOptStatus] = useState<OptimizationStatus | null>(null);
  const [optDecisions, setOptDecisions] = useState<OptimizationDecisionRecord[]>([]);
  const [showDecisionsModal, setShowDecisionsModal] = useState(false);
  const [revertingDecision, setRevertingDecision] = useState<OptimizationDecisionRecord | null>(null);
  const [revertLoading, setRevertLoading] = useState(false);
  const [revertFeedback, setRevertFeedback] = useState<{ type: "success" | "danger"; message: string } | null>(null);

  // Search and status filter
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"ALL" | "1" | "0">("ALL");

  // Status toggle confirmation
  const [pendingToggle, setPendingToggle] = useState<{ id: number; currentStatus: number; name: string } | null>(null);
  const [toggleLoading, setToggleLoading] = useState(false);

  const reload = async () => {
    try {
      setLoading(true);
      const [c, a] = await Promise.all([listCampaigns(), listAdvertisers()]);
      setRows(c);
      setAdvertisers(a);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load campaigns");
    } finally {
      setLoading(false);
    }

    try {
      const [status, decisions] = await Promise.all([
        getOptimizationStatus(),
        listOptimizationDecisions({ limit: 30 }),
      ]);
      setOptStatus(status);
      setOptDecisions(decisions);
    } catch {
      // Non-fatal if optimization stats fail
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  const confirmToggleStatus = async () => {
    if (!pendingToggle) return;
    setToggleLoading(true);
    try {
      const nextStatus = pendingToggle.currentStatus === 1 ? 0 : 1;
      await setCampaignStatus(pendingToggle.id, nextStatus);
      setPendingToggle(null);
      await reload();
    } catch (err: any) {
      setError(err.message || "Failed to update campaign status");
    } finally {
      setToggleLoading(false);
    }
  };

  const handleConfirmRevert = async () => {
    if (!revertingDecision) return;
    setRevertLoading(true);
    setRevertFeedback(null);
    try {
      await revertOptimizationDecision(revertingDecision.decision_id);
      setRevertFeedback({
        type: "success",
        message: `Decision #${revertingDecision.decision_id.slice(0, 8)} successfully reverted.`,
      });
      setRevertingDecision(null);
      await reload();
    } catch (err: any) {
      setRevertFeedback({
        type: "danger",
        message: err.message || "Failed to revert decision",
      });
    } finally {
      setRevertLoading(false);
    }
  };

  const filteredRows = rows.filter((c) => {
    if (statusFilter !== "ALL" && String(c.status) !== statusFilter) return false;
    if (search.trim()) {
      const q = search.toLowerCase();
      const advName = advertisers.find((a) => a.id === c.advertiser_id)?.name.toLowerCase() || "";
      return c.name.toLowerCase().includes(q) || String(c.id).includes(q) || advName.includes(q);
    }
    return true;
  });

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Platform Directory"
        title="All Campaigns"
        description="Platform-wide campaign management, daily pacing progress, and direct publisher inventory assignments."
        actions={
          <Button variant="secondary" size="sm" onClick={reload} isLoading={loading}>
            Refresh
          </Button>
        }
      />

      {error && (
        <Alert variant="danger" title="Campaign Error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {revertFeedback && (
        <Alert
          variant={revertFeedback.type}
          title={revertFeedback.type === "success" ? "Decision Reverted" : "Revert Failed"}
          onClose={() => setRevertFeedback(null)}
        >
          {revertFeedback.message}
        </Alert>
      )}

      {/* Laya Optimizer & Pilot Governance Card */}
      {optStatus && (
        <Card
          title="Laya Optimizer &amp; Pilot Governance"
          subtitle="Autonomous decisioning policy controls, limited-pilot gating, and audit safeguards."
          style={{ marginBottom: "20px" }}
        >
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "16px" }}>
            <Button
              variant={showDecisionsModal ? "primary" : "secondary"}
              size="sm"
              onClick={() => setShowDecisionsModal(!showDecisionsModal)}
            >
              {showDecisionsModal ? "Hide Decision Audit Log" : `View Decision Audit Log (${optDecisions.length})`}
            </Button>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: "16px",
              marginBottom: "16px",
            }}
          >
            <div style={{ padding: "12px", background: "var(--color-bg-subtle)", borderRadius: "var(--radius-md)" }}>
              <div style={{ fontSize: "11px", color: "var(--color-text-muted)", textTransform: "uppercase", fontWeight: 600 }}>
                Global Operating Mode
              </div>
              <div style={{ marginTop: "6px", display: "flex", alignItems: "center", gap: "8px" }}>
                <StatusBadge
                  status={optStatus.global_mode === "limited_pilot" ? "warning" : optStatus.global_mode === "autopilot" ? "active" : "paused"}
                  label={optStatus.global_mode.toUpperCase()}
                  size="sm"
                />
                <span style={{ fontSize: "12px", color: "var(--color-text-muted)" }}>
                  {optStatus.global_mode === "suggestions_only" ? "Safe default (human-in-the-loop)" : optStatus.global_mode === "limited_pilot" ? "Allowlist only" : "Autonomous"}
                </span>
              </div>
            </div>

            <div style={{ padding: "12px", background: "var(--color-bg-subtle)", borderRadius: "var(--radius-md)" }}>
              <div style={{ fontSize: "11px", color: "var(--color-text-muted)", textTransform: "uppercase", fontWeight: 600 }}>
                Global Kill Switch
              </div>
              <div style={{ marginTop: "6px", display: "flex", alignItems: "center", gap: "8px" }}>
                <StatusBadge
                  status={optStatus.kill_switch ? "error" : "success"}
                  label={optStatus.kill_switch ? "ENGAGED (HALTED)" : "DISENGAGED (NORMAL)"}
                  size="sm"
                />
              </div>
            </div>

            <div style={{ padding: "12px", background: "var(--color-bg-subtle)", borderRadius: "var(--radius-md)" }}>
              <div style={{ fontSize: "11px", color: "var(--color-text-muted)", textTransform: "uppercase", fontWeight: 600 }}>
                Pilot Allowlist &amp; Safety Gate
              </div>
              <div style={{ marginTop: "6px", fontSize: "13px", fontWeight: 600 }}>
                {optStatus.global_mode === "limited_pilot" && optStatus.pilot_campaign_ids.length === 0 ? (
                  <span style={{ color: "var(--color-danger)" }}>
                    ⚠️ 0 campaigns allowlisted (failing closed)
                  </span>
                ) : (
                  <span>
                    {optStatus.pilot_campaign_ids.length} campaigns allowlisted
                    {optStatus.pilot_campaign_ids.length > 0 && (
                      <span style={{ fontSize: "11px", color: "var(--color-text-muted)", display: "block" }}>
                        IDs: [{optStatus.pilot_campaign_ids.join(", ")}]
                      </span>
                    )}
                  </span>
                )}
              </div>
            </div>

            <div style={{ padding: "12px", background: "var(--color-bg-subtle)", borderRadius: "var(--radius-md)" }}>
              <div style={{ fontSize: "11px", color: "var(--color-text-muted)", textTransform: "uppercase", fontWeight: 600 }}>
                Execution Safeguards
              </div>
              <div style={{ marginTop: "6px", fontSize: "12px" }}>
                <div>Max Delta: <strong>±${optStatus.pilot_max_budget_delta_amount}</strong></div>
                <div>Max Daily Changes: <strong>{optStatus.max_daily_changes} / campaign</strong></div>
                <div>Cooldown: <strong>{optStatus.cooldown_seconds}s</strong></div>
              </div>
            </div>
          </div>

          {optStatus.global_mode === "limited_pilot" && optStatus.pilot_campaign_ids.length === 0 && (
            <Alert variant="warning" style={{ marginTop: "8px" }}>
              <strong>Fail-Closed Safety Active:</strong> Global mode is set to <code>limited_pilot</code> but the allowlist is empty. No automated actions will execute on any campaign until campaigns are explicitly allowlisted.
            </Alert>
          )}

          {/* Collapsible Decision Audit Log */}
          {showDecisionsModal && (
            <div style={{ marginTop: "16px", borderTop: "1px solid var(--color-border)", paddingTop: "16px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
                <h4 style={{ margin: 0, fontSize: "14px", fontWeight: 600 }}>
                  Recent Autonomous Decisions &amp; Actions ({optDecisions.length})
                </h4>
                <Button variant="secondary" size="sm" onClick={reload}>
                  Refresh Audit Trail
                </Button>
              </div>

              {optDecisions.length === 0 ? (
                <EmptyState
                  title="No optimization decisions recorded"
                  description="Decisions will appear here as Laya evaluates campaigns during scheduled cycles."
                />
              ) : (
                <div className="table-container" style={{ overflowX: "auto" }}>
                  <table className="table" style={{ fontSize: "12px" }}>
                    <thead>
                      <tr>
                        <th>Time</th>
                        <th>Campaign</th>
                        <th>Mode</th>
                        <th>Action</th>
                        <th>Confidence</th>
                        <th>Outcome</th>
                        <th>Reason / Evidence</th>
                        <th style={{ textAlign: "right" }}>Rollback</th>
                      </tr>
                    </thead>
                    <tbody>
                      {optDecisions.map((d) => {
                        const isApplied = d.outcome === "applied";
                        return (
                          <tr key={d.decision_id}>
                            <td style={{ whiteSpace: "nowrap", color: "var(--color-text-muted)" }}>
                              {new Date(d.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                              <div style={{ fontSize: "10px" }}>{new Date(d.created_at).toLocaleDateString()}</div>
                            </td>
                            <td>
                              <strong>Campaign #{d.campaign_id}</strong>
                            </td>
                            <td>
                              <span style={{ fontSize: "11px", textTransform: "capitalize" }}>{d.mode}</span>
                            </td>
                            <td>
                              <code>{d.action || "no_action"}</code>
                            </td>
                            <td>
                              {d.confidence != null ? `${Math.round(d.confidence * 100)}%` : "—"}
                            </td>
                            <td>
                              <StatusBadge
                                status={
                                  d.outcome === "applied"
                                    ? "active"
                                    : d.outcome === "suggested"
                                    ? "pending"
                                    : d.outcome === "reverted"
                                    ? "paused"
                                    : d.outcome === "rejected"
                                    ? "warning"
                                    : "error"
                                }
                                label={d.outcome.toUpperCase()}
                                size="sm"
                              />
                            </td>
                            <td style={{ maxWidth: "260px", wordBreak: "break-word" }}>
                              {d.reason || "—"}
                            </td>
                            <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                              {isApplied && (
                                <Button
                                  variant="danger"
                                  size="sm"
                                  onClick={() => setRevertingDecision(d)}
                                >
                                  Revert
                                </Button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </Card>
      )}

      <Card
        title="Active &amp; Archived Campaigns"
        subtitle={`${rows.length} total campaigns configured across all advertiser accounts.`}
      >
        {/* Controls: Search and Status Filter */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "12px",
            marginBottom: "16px",
          }}
        >
          <div style={{ display: "flex", gap: "8px" }}>
            <button
              type="button"
              className={`btn ${statusFilter === "ALL" ? "btn-primary" : "btn-secondary"} btn-sm`}
              onClick={() => setStatusFilter("ALL")}
            >
              All ({rows.length})
            </button>
            <button
              type="button"
              className={`btn ${statusFilter === "1" ? "btn-primary" : "btn-secondary"} btn-sm`}
              onClick={() => setStatusFilter("1")}
            >
              Active ({rows.filter((c) => c.status === 1).length})
            </button>
            <button
              type="button"
              className={`btn ${statusFilter === "0" ? "btn-primary" : "btn-secondary"} btn-sm`}
              onClick={() => setStatusFilter("0")}
            >
              Paused ({rows.filter((c) => c.status === 0).length})
            </button>
          </div>

          <div style={{ minWidth: "240px" }}>
            <input
              type="text"
              placeholder="Search by name, ID, or advertiser..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="input"
              style={{ fontSize: "0.8125rem", padding: "6px 10px" }}
            />
          </div>
        </div>

        {loading ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "10px", padding: "16px 0" }}>
            <LoadingSkeleton variant="rect" height={40} />
            <LoadingSkeleton variant="rect" height={40} />
            <LoadingSkeleton variant="rect" height={40} />
          </div>
        ) : filteredRows.length === 0 ? (
          <EmptyState
            title="No campaigns found"
            description={search ? "No campaigns match your query." : "No campaigns registered in the system."}
          />
        ) : (
          <div className="table-container" style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Campaign Name &amp; ID</th>
                  <th>Advertiser</th>
                  <th>Bid Amount</th>
                  <th>Pacing / Today</th>
                  <th>Impressions</th>
                  <th>Clicks</th>
                  <th>CTR</th>
                  <th>Status</th>
                  <th style={{ textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((c) => {
                  const adv = advertisers.find((a) => a.id === c.advertiser_id);
                  const isActive = c.status === 1;
                  const pct = Number(c.budget_daily) ? Math.min(100, Math.round((Number(c.spent_today) / Number(c.budget_daily)) * 100)) : 0;

                  return (
                    <tr key={c.id}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{c.name}</div>
                        <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>
                          ID #{c.id}
                        </div>
                      </td>
                      <td>
                        <div style={{ fontSize: "0.8125rem" }}>
                          {adv ? adv.name : `#${c.advertiser_id}`}
                        </div>
                      </td>
                      <td style={{ fontSize: "0.8125rem", fontWeight: 600 }}>
                        {BID_TYPES[c.bid_type] || "CPM"} ${c.bid_amount}
                      </td>
                      <td>
                        <div style={{ fontSize: "0.8125rem", fontWeight: 600 }}>
                          {money(c.spent_today)} / {money(c.budget_daily)}
                        </div>
                        <div
                          style={{
                            height: "4px",
                            width: "100%",
                            backgroundColor: "var(--color-bg-subtle)",
                            borderRadius: "var(--radius-full)",
                            overflow: "hidden",
                            marginTop: "4px",
                          }}
                        >
                          <div
                            style={{
                              height: "100%",
                              width: `${pct}%`,
                              backgroundColor: pct > 90 ? "var(--color-danger)" : "var(--color-primary)",
                            }}
                          />
                        </div>
                      </td>
                      <td style={{ fontSize: "0.8125rem" }}>{num(c.impressions)}</td>
                      <td style={{ fontSize: "0.8125rem" }}>{num(c.clicks)}</td>
                      <td style={{ fontSize: "0.8125rem", fontWeight: 600 }}>
                        {ctr(c.clicks, c.impressions)}
                      </td>
                      <td>
                        <StatusBadge 
                          status={isActive ? "active" : "paused"} 
                          label={isActive ? "Active" : "Paused"} 
                          size="sm" 
                        />
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <div style={{ display: "flex", gap: "6px", justifyContent: "flex-end" }}>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => setAssigningCampaign(c)}
                          >
                            Assign
                          </Button>
                          <Button
                            variant={isActive ? "secondary" : "primary"}
                            size="sm"
                            onClick={() => setPendingToggle({ id: c.id, currentStatus: c.status, name: c.name })}
                          >
                            {isActive ? "Pause" : "Activate"}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Assignment Modal */}
      {assigningCampaign && (
        <CampaignAssignmentModal
          campaign={assigningCampaign}
          onClose={() => setAssigningCampaign(null)}
        />
      )}

      {/* Confirmation Dialog for Status Change */}
      <ConfirmDialog
        isOpen={!!pendingToggle}
        title={pendingToggle?.currentStatus === 1 ? "Pause Campaign" : "Activate Campaign"}
        message={
          pendingToggle?.currentStatus === 1
            ? `Are you sure you want to pause "${pendingToggle?.name}"? Ad sets and creatives under this campaign will cease serving.`
            : `Are you sure you want to activate "${pendingToggle?.name}"? The campaign will resume serving ads according to its targeting and daily pacing.`
        }
        confirmText={pendingToggle?.currentStatus === 1 ? "Pause Campaign" : "Activate Campaign"}
        variant={pendingToggle?.currentStatus === 1 ? "danger" : "primary"}
        isLoading={toggleLoading}
        onConfirm={confirmToggleStatus}
        onCancel={() => setPendingToggle(null)}
      />

      {/* Confirmation Dialog for Optimization Decision Revert */}
      <ConfirmDialog
        isOpen={!!revertingDecision}
        title="Revert Optimization Decision"
        message={`Are you sure you want to revert decision ${revertingDecision?.decision_id.slice(0, 8)} on Campaign #${revertingDecision?.campaign_id}? This will safely roll back the applied daily budget change or targeting rule and log an immutable audit record.`}
        confirmText="Confirm Revert"
        variant="danger"
        isLoading={revertLoading}
        onConfirm={handleConfirmRevert}
        onCancel={() => setRevertingDecision(null)}
      />
    </PageContainer>
  );
}
