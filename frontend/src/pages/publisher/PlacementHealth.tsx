import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import {
  getPlacementHealth,
  type PlacementHealthResponse,
  type PlacementHealthItem,
  type FormatBenchmark,
} from "../../api/client";
import { money, num } from "../../lib/format";
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

type VerdictFilter = "ALL" | "great" | "healthy" | "low_visibility" | "insufficient_data";

export default function PlacementHealth() {
  const { session } = useAuth();
  const publisherId = session?.user.publisher_id;

  const [data, setData] = useState<PlacementHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<VerdictFilter>("ALL");
  const [search, setSearch] = useState("");

  const loadData = async () => {
    if (!publisherId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await getPlacementHealth(publisherId, 30);
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load placement health data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, [publisherId]);

  if (!publisherId) {
    return (
      <PageContainer>
        <Card>
          <EmptyState
            title="Publisher Account Required"
            description="You must be logged in as a publisher to view placement health diagnostics."
          />
        </Card>
      </PageContainer>
    );
  }

  const placements = data?.placements || [];
  const filteredPlacements = placements.filter((p) => {
    if (filter !== "ALL" && p.verdict !== filter) return false;
    if (search.trim()) {
      const q = search.toLowerCase();
      return (
        p.slot_name.toLowerCase().includes(q) ||
        p.slot_id.toLowerCase().includes(q) ||
        p.format_name.toLowerCase().includes(q)
      );
    }
    return true;
  });

  const countGreat = placements.filter((p) => p.verdict === "great").length;
  const countHealthy = placements.filter((p) => p.verdict === "healthy").length;
  const countLowVis = placements.filter((p) => p.verdict === "low_visibility").length;
  const countInsufficient = placements.filter((p) => p.verdict === "insufficient_data").length;

  const renderVerdictBadge = (verdict: string) => {
    switch (verdict) {
      case "great":
        return <StatusBadge status="success" label="★ Great" size="sm" />;
      case "healthy":
        return <StatusBadge status="active" label="✓ Healthy" size="sm" />;
      case "low_visibility":
        return <StatusBadge status="warning" label="⚠ Low Visibility" size="sm" />;
      case "insufficient_data":
      default:
        return <StatusBadge status="pending" label="⏳ Insufficient Data" size="sm" />;
    }
  };

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Inventory Diagnostics"
        title="Placement Health Analysis"
        description="Automated performance benchmarking and health classification for your ad inventory."
        actions={
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            {data && (
              <span style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                Rolling 30 Days: <strong>{data.start_date}</strong> to <strong>{data.end_date}</strong>
              </span>
            )}
            <Button
              variant="secondary"
              size="sm"
              onClick={loadData}
              isLoading={loading}
            >
              Refresh
            </Button>
          </div>
        }
      />

      {error && (
        <Alert variant="danger" title="Error Loading Data" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Summary KPI Cards */}
      <div
        className="grid cards"
        style={{
          marginBottom: "24px",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: "16px",
        }}
      >
        <Card style={{ margin: 0, padding: "16px" }}>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", textTransform: "uppercase", fontWeight: 600 }}>
            Total Placements
          </div>
          <div style={{ fontSize: "1.75rem", fontWeight: 700, marginTop: "6px" }}>
            {data ? num(data.total_placements) : "—"}
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "4px" }}>
            Active inventory slots
          </div>
        </Card>

        <Card style={{ margin: 0, padding: "16px", borderLeft: "4px solid var(--color-success)" }}>
          <div style={{ color: "var(--color-success)", fontSize: "0.75rem", textTransform: "uppercase", fontWeight: 600 }}>
            Great (High Yield)
          </div>
          <div style={{ fontSize: "1.75rem", fontWeight: 700, marginTop: "6px", color: "var(--color-success)" }}>
            {data ? num(countGreat) : "—"}
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "4px" }}>
            Outperforming platform median
          </div>
        </Card>

        <Card style={{ margin: 0, padding: "16px", borderLeft: "4px solid var(--color-primary)" }}>
          <div style={{ color: "var(--color-primary)", fontSize: "0.75rem", textTransform: "uppercase", fontWeight: 600 }}>
            Healthy (Consistent)
          </div>
          <div style={{ fontSize: "1.75rem", fontWeight: 700, marginTop: "6px", color: "var(--color-primary)" }}>
            {data ? num(countHealthy) : "—"}
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "4px" }}>
            Consistent delivery metrics
          </div>
        </Card>

        <Card style={{ margin: 0, padding: "16px", borderLeft: "4px solid #f59e0b" }}>
          <div style={{ color: "#b45309", fontSize: "0.75rem", textTransform: "uppercase", fontWeight: 600 }}>
            Low Visibility (&lt; 0.2%)
          </div>
          <div style={{ fontSize: "1.75rem", fontWeight: 700, marginTop: "6px", color: "#b45309" }}>
            {data ? num(countLowVis) : "—"}
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "4px" }}>
            Needs position adjustment
          </div>
        </Card>

        <Card style={{ margin: 0, padding: "16px", borderLeft: "4px solid #94a3b8" }}>
          <div style={{ color: "#475569", fontSize: "0.75rem", textTransform: "uppercase", fontWeight: 600 }}>
            Insufficient Data
          </div>
          <div style={{ fontSize: "1.75rem", fontWeight: 700, marginTop: "6px", color: "#475569" }}>
            {data ? num(countInsufficient) : "—"}
          </div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "4px" }}>
            Traffic &lt; 100 impressions
          </div>
        </Card>
      </div>

      {/* Platform Benchmarks Reference Card */}
      {data && data.benchmarks && Object.keys(data.benchmarks).length > 0 && (
        <Card
          title="Platform Benchmark Medians (Format-Specific)"
          subtitle="Computed across active platform placements (minimum 100 impressions). Comparisons are strictly format-to-format."
          style={{ marginBottom: "24px" }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: "12px",
            }}
          >
            {Object.entries(data.benchmarks).map(([fmtKey, bm]: [string, FormatBenchmark]) => (
              <div
                key={fmtKey}
                style={{
                  backgroundColor: "var(--color-bg-subtle)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "var(--radius-md)",
                  padding: "12px 14px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <strong>{bm.format_name || fmtKey}</strong>
                  <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>
                    {bm.eligible_slots_count} eligible slots
                  </span>
                </div>
                <div style={{ display: "flex", gap: "16px", marginTop: "8px", fontSize: "13px" }}>
                  <div>
                    <span style={{ fontSize: "11px", color: "var(--color-text-muted)", display: "block" }}>
                      Median CTR
                    </span>
                    <strong>{bm.median_ctr !== null ? (bm.median_ctr * 100).toFixed(2) + "%" : "N/A"}</strong>
                  </div>
                  <div>
                    <span style={{ fontSize: "11px", color: "var(--color-text-muted)", display: "block" }}>
                      Median eCPM
                    </span>
                    <strong>{bm.median_ecpm !== null ? money(bm.median_ecpm) : "N/A"}</strong>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Placements Detailed Analysis Table */}
      <Card
        title="Placement Diagnostics &amp; Recommendations"
        subtitle="Individual unit performance and actionable optimization steps."
      >
        {/* Controls: Filter Chips and Search */}
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
          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
            {[
              { id: "ALL", label: `All (${placements.length})` },
              { id: "great", label: `Great (${countGreat})` },
              { id: "healthy", label: `Healthy (${countHealthy})` },
              { id: "low_visibility", label: `Low Vis (${countLowVis})` },
              { id: "insufficient_data", label: `Insufficient (${countInsufficient})` },
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                className={`btn ${filter === tab.id ? "btn-primary" : "btn-secondary"} btn-sm`}
                onClick={() => setFilter(tab.id as VerdictFilter)}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div style={{ minWidth: "220px" }}>
            <input
              type="text"
              placeholder="Search placement or format..."
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
        ) : filteredPlacements.length === 0 ? (
          <EmptyState
            title="No placements match filter"
            description="Adjust your search query or verdict category filter."
          />
        ) : (
          <div className="table-container" style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Placement</th>
                  <th>Format</th>
                  <th style={{ textAlign: "right" }}>30d Volume</th>
                  <th style={{ textAlign: "right" }}>CTR vs Benchmark</th>
                  <th style={{ textAlign: "right" }}>eCPM vs Benchmark</th>
                  <th>Verdict</th>
                  <th>Optimization Recommendation</th>
                  <th style={{ textAlign: "right" }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredPlacements.map((p) => {
                  const hasBenchmark = p.benchmark && p.benchmark.median_ctr !== null;
                  const ctrDiff = hasBenchmark && p.impressions >= 100 ? p.ctr - (p.benchmark.median_ctr || 0) : null;
                  const ecpmDiff = hasBenchmark && p.impressions >= 100 && p.benchmark.median_ecpm !== null ? p.ecpm - p.benchmark.median_ecpm : null;

                  return (
                    <tr key={p.slot_id}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{p.slot_name}</div>
                        <div style={{ fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--color-text-muted)" }}>
                          {p.slot_id}
                        </div>
                      </td>
                      <td>
                        <span style={{ fontSize: "11px", background: "var(--color-bg-subtle)", padding: "2px 6px", borderRadius: "4px", textTransform: "uppercase", fontWeight: 600 }}>
                          {p.format_name}
                        </span>
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <div>{num(p.impressions)} imps</div>
                        <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>{num(p.clicks)} clicks</div>
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <div style={{ fontWeight: 600 }}>{(p.ctr * 100).toFixed(2)}%</div>
                        {ctrDiff !== null ? (
                          <div style={{ fontSize: "11px", color: ctrDiff >= 0 ? "var(--color-success)" : "var(--color-danger)" }}>
                            {ctrDiff >= 0 ? "+" : ""}{(ctrDiff * 100).toFixed(2)}% vs bm
                          </div>
                        ) : (
                          <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>—</div>
                        )}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <div style={{ fontWeight: 600 }}>{money(p.ecpm)}</div>
                        {ecpmDiff !== null ? (
                          <div style={{ fontSize: "11px", color: ecpmDiff >= 0 ? "var(--color-success)" : "var(--color-danger)" }}>
                            {ecpmDiff >= 0 ? "+" : ""}{money(ecpmDiff)} vs bm
                          </div>
                        ) : (
                          <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>—</div>
                        )}
                      </td>
                      <td>
                        {renderVerdictBadge(p.verdict)}
                      </td>
                      <td style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)", maxWidth: "320px" }}>
                        {p.recommendation}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <Link
                          to={`/publisher/preview?slot_id=${encodeURIComponent(p.slot_id)}`}
                          className="btn btn-secondary btn-sm"
                        >
                          Preview
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </PageContainer>
  );
}
