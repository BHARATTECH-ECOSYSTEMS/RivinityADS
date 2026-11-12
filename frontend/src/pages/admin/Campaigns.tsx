import React, { useEffect, useState } from "react";
import { 
  listAdvertisers, 
  listCampaigns, 
  setCampaignStatus 
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
    </PageContainer>
  );
}
