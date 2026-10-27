import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { 
  getHealth, 
  listAdvertisers, 
  listCampaigns, 
  listPublishers,
} from "../../api/client";
import StatCard from "../../components/StatCard";
import { money, num } from "../../lib/format";
import type { HealthResponse, Advertiser, Campaign, Publisher } from "../../types";
import {
  PageContainer,
  PageHeader,
  Card,
  Button,
  StatusBadge,
  Alert,
  LoadingSkeleton,
  EmptyState,
} from "../../components/ui";

export default function AdminDashboard() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [loadingHealth, setLoadingHealth] = useState(true);

  const [advertisers, setAdvertisers] = useState<Advertiser[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [publishers, setPublishers] = useState<Publisher[]>([]);
  const [loadingStats, setLoadingStats] = useState(true);

  // Search & Investigation query
  const [searchQuery, setSearchQuery] = useState("");

  const reloadData = async () => {
    setLoadingHealth(true);
    setLoadingStats(true);

    getHealth()
      .then(setHealth)
      .catch((e) => setHealthError(e instanceof Error ? e.message : "Backend unreachable"))
      .finally(() => setLoadingHealth(false));

    Promise.all([listAdvertisers(), listCampaigns(), listPublishers()])
      .then(([advs, camps, pubs]) => {
        setAdvertisers(advs);
        setCampaigns(camps);
        setPublishers(pubs);
      })
      .catch(() => {})
      .finally(() => setLoadingStats(false));
  };

  useEffect(() => {
    void reloadData();
  }, []);

  const totalImpressions = campaigns.reduce((s, c) => s + c.impressions, 0);
  const totalSpend = campaigns.reduce((s, c) => s + c.spent_total, 0);
  const activeCampaignsCount = campaigns.filter((c) => c.status === 1).length;

  // Search filter
  const q = searchQuery.trim().toLowerCase();
  const matchedAdvertisers = q ? advertisers.filter((a) => a.name.toLowerCase().includes(q) || String(a.id).includes(q) || (a.company && a.company.toLowerCase().includes(q))) : [];
  const matchedCampaigns = q ? campaigns.filter((c) => c.name.toLowerCase().includes(q) || String(c.id).includes(q)) : [];
  const matchedPublishers = q ? publishers.filter((p) => p.name.toLowerCase().includes(q) || String(p.id).includes(q)) : [];
  const hasSearchResults = q.length > 0 && (matchedAdvertisers.length > 0 || matchedCampaigns.length > 0 || matchedPublishers.length > 0);

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Platform Administration"
        title="Platform Operations"
        description="System health telemetry, core entity overviews, fast investigation lookup, and ledger reconciliation."
        actions={
          <div style={{ display: "flex", gap: "8px" }}>
            <Link to="/admin/creatives" className="btn btn-secondary btn-sm">
              Creative Moderation
            </Link>
            <Link to="/admin/finance" className="btn btn-secondary btn-sm">
              Reconcile Ledger
            </Link>
            <Button variant="secondary" size="sm" onClick={reloadData} isLoading={loadingHealth || loadingStats}>
              Refresh
            </Button>
          </div>
        }
      />

      {healthError && (
        <Alert variant="danger" title="Service Unreachable">
          Could not communicate with the ad server backend: {healthError}. Verify that FastAPI is running on port 8000.
        </Alert>
      )}

      {/* High-Level Overview Metrics */}
      {loadingStats ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: "16px", marginBottom: "24px" }}>
          <LoadingSkeleton variant="rect" height={90} />
          <LoadingSkeleton variant="rect" height={90} />
          <LoadingSkeleton variant="rect" height={90} />
          <LoadingSkeleton variant="rect" height={90} />
          <LoadingSkeleton variant="rect" height={90} />
        </div>
      ) : (
        <div className="grid cards" style={{ marginBottom: "24px", gridTemplateColumns: "repeat(5, 1fr)" }}>
          <StatCard title="Advertisers" value={num(advertisers.length)} />
          <StatCard title="Campaigns" value={`${num(activeCampaignsCount)} / ${num(campaigns.length)}`} hint="active / total" />
          <StatCard title="Impressions" value={num(totalImpressions)} />
          <StatCard title="Total Spend" value={money(totalSpend)} />
          <StatCard title="Publishers" value={num(publishers.length)} />
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1.8fr 2.2fr", gap: "24px", alignItems: "start", marginBottom: "24px" }}>
        
        {/* Backend & Infrastructure Health */}
        <Card
          title="Backend Infrastructure Health"
          subtitle="Real-time connectivity to core stateful dependencies."
        >
          {loadingHealth ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px", padding: "12px 0" }}>
              <LoadingSkeleton variant="rect" height={30} />
              <LoadingSkeleton variant="rect" height={30} />
            </div>
          ) : health ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 14px", background: "var(--color-bg-subtle)", borderRadius: "var(--radius-md)" }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: "0.875rem" }}>Service Status</div>
                  <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>FastAPI Ad Engine</div>
                </div>
                <StatusBadge 
                  status={health.status === "healthy" ? "success" : "warning"} 
                  label={health.status.toUpperCase()} 
                  size="sm" 
                />
              </div>

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 14px", background: "var(--color-bg-subtle)", borderRadius: "var(--radius-md)" }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: "0.875rem" }}>PostgreSQL Database</div>
                  <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>Transactional storage &amp; ledger</div>
                </div>
                <StatusBadge 
                  status={health.database ? "success" : "failed"} 
                  label={health.database ? "Connected" : "Down"} 
                  size="sm" 
                />
              </div>

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 14px", background: "var(--color-bg-subtle)", borderRadius: "var(--radius-md)" }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: "0.875rem" }}>Redis Cache &amp; Counters</div>
                  <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>Rate limits &amp; pacing counters</div>
                </div>
                <StatusBadge 
                  status={health.redis ? "success" : "failed"} 
                  label={health.redis ? "Connected" : "Down"} 
                  size="sm" 
                />
              </div>

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 14px", fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                <span>Engine Version</span>
                <code style={{ fontFamily: "var(--font-mono)" }}>v{health.version || "0.1.0"}</code>
              </div>
            </div>
          ) : (
            <p style={{ color: "var(--color-text-muted)" }}>Health status unavailable.</p>
          )}

          <div style={{ marginTop: "16px", padding: "12px", background: "var(--color-bg-subtle)", borderRadius: "var(--radius-md)", fontSize: "0.75rem", color: "var(--color-text-secondary)", lineHeight: 1.5 }}>
            <strong>Operational Monitoring Notice:</strong> Background worker execution (webhook delivery, email notifications, budget pacing) operates via outbox lease/claim workers. Dedicated server telemetry endpoint for workers is managed at infrastructure level.
          </div>
        </Card>

        {/* Entity Search & Investigation */}
        <Card
          title="Search &amp; Investigation"
          subtitle="Instant lookup across registered advertisers, campaigns, and publishers."
        >
          <div style={{ marginBottom: "16px" }}>
            <input
              type="text"
              placeholder="Search by name, company, or ID..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="input"
              style={{ width: "100%" }}
            />
          </div>

          {!q ? (
            <div style={{ textAlign: "center", padding: "24px 0", color: "var(--color-text-muted)", fontSize: "0.875rem" }}>
              Type a search query above to look up advertisers, campaigns, or publishers.
            </div>
          ) : !hasSearchResults ? (
            <EmptyState
              title="No entities match search"
              description={`No advertisers, campaigns, or publishers found for "${searchQuery}".`}
            />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px", maxHeight: "360px", overflowY: "auto" }}>
              {matchedAdvertisers.length > 0 && (
                <div>
                  <div style={{ fontSize: "0.75rem", fontWeight: 700, color: "var(--color-text-muted)", textTransform: "uppercase", marginBottom: "6px" }}>
                    Advertisers ({matchedAdvertisers.length})
                  </div>
                  {matchedAdvertisers.map((a) => (
                    <div key={a.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px", borderBottom: "1px solid var(--border-color)" }}>
                      <div>
                        <strong>{a.name}</strong> <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>ID #{a.id}</span>
                        {a.company && <div style={{ fontSize: "12px", color: "var(--color-text-secondary)" }}>{a.company}</div>}
                      </div>
                      <Link to="/admin/advertisers" className="btn btn-secondary btn-sm">
                        View
                      </Link>
                    </div>
                  ))}
                </div>
              )}

              {matchedCampaigns.length > 0 && (
                <div>
                  <div style={{ fontSize: "0.75rem", fontWeight: 700, color: "var(--color-text-muted)", textTransform: "uppercase", marginBottom: "6px" }}>
                    Campaigns ({matchedCampaigns.length})
                  </div>
                  {matchedCampaigns.map((c) => (
                    <div key={c.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px", borderBottom: "1px solid var(--border-color)" }}>
                      <div>
                        <strong>{c.name}</strong> <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>ID #{c.id}</span>
                        <div style={{ fontSize: "12px", color: "var(--color-text-secondary)" }}>
                          Spent Today: {money(c.spent_today)} / {money(c.budget_daily)}
                        </div>
                      </div>
                      <Link to="/admin/campaigns" className="btn btn-secondary btn-sm">
                        View
                      </Link>
                    </div>
                  ))}
                </div>
              )}

              {matchedPublishers.length > 0 && (
                <div>
                  <div style={{ fontSize: "0.75rem", fontWeight: 700, color: "var(--color-text-muted)", textTransform: "uppercase", marginBottom: "6px" }}>
                    Publishers ({matchedPublishers.length})
                  </div>
                  {matchedPublishers.map((p) => (
                    <div key={p.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px", borderBottom: "1px solid var(--border-color)" }}>
                      <div>
                        <strong>{p.name}</strong> <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>ID #{p.id}</span>
                        {p.email && <div style={{ fontSize: "12px", color: "var(--color-text-secondary)" }}>{p.email}</div>}
                      </div>
                      <Link to="/admin/publishers" className="btn btn-secondary btn-sm">
                        View
                      </Link>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    </PageContainer>
  );
}
