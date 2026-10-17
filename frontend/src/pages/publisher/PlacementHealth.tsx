import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import {
  getPlacementHealth,
  type PlacementHealthResponse,
  type PlacementHealthItem,
  type FormatBenchmark,
} from "../../api/client";
import { money, num } from "../../lib/format";

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
      <div className="card" style={{ padding: "32px", textAlign: "center" }}>
        <h2>Publisher Account Required</h2>
        <p className="muted">You must be logged in as a publisher to view placement health.</p>
      </div>
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
        return (
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "4px",
              padding: "4px 10px",
              borderRadius: "999px",
              fontSize: "12px",
              fontWeight: 700,
              backgroundColor: "#dcfce7",
              color: "#15803d",
              border: "1px solid #bbf7d0",
            }}
          >
            ★ Great
          </span>
        );
      case "healthy":
        return (
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "4px",
              padding: "4px 10px",
              borderRadius: "999px",
              fontSize: "12px",
              fontWeight: 700,
              backgroundColor: "#eff6ff",
              color: "#1d4ed8",
              border: "1px solid #bfdbfe",
            }}
          >
            ✓ Healthy
          </span>
        );
      case "low_visibility":
        return (
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "4px",
              padding: "4px 10px",
              borderRadius: "999px",
              fontSize: "12px",
              fontWeight: 700,
              backgroundColor: "#fef3c7",
              color: "#b45309",
              border: "1px solid #fde68a",
            }}
          >
            ⚠ Low Visibility
          </span>
        );
      case "insufficient_data":
      default:
        return (
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "4px",
              padding: "4px 10px",
              borderRadius: "999px",
              fontSize: "12px",
              fontWeight: 600,
              backgroundColor: "#f1f5f9",
              color: "#475569",
              border: "1px solid #cbd5e1",
            }}
          >
            ⏳ Insufficient Data
          </span>
        );
    }
  };

  const renderFormatBadge = (formatName: string) => (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: "4px",
        fontSize: "11px",
        fontWeight: 600,
        textTransform: "uppercase",
        backgroundColor: "#e2e8f0",
        color: "#334155",
        letterSpacing: "0.03em",
      }}
    >
      {formatName}
    </span>
  );

  return (
    <div style={{ maxWidth: "1200px", margin: "0 auto" }}>
      {/* Header Banner */}
      <div
        className="flex row space-between align-center"
        style={{ marginBottom: "20px", flexWrap: "wrap", gap: "12px" }}
      >
        <div>
          <h1 style={{ marginBottom: "4px" }}>Placement Health Analysis</h1>
          <p className="muted" style={{ margin: 0 }}>
            Automated performance benchmarking and health classification for your ad inventory.
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          {data && (
            <div
              style={{
                background: "#ffffff",
                border: "1px solid #cbd5e1",
                borderRadius: "8px",
                padding: "6px 12px",
                fontSize: "13px",
                color: "#475569",
                fontWeight: 500,
              }}
            >
              📅 Rolling 30 Days: <strong>{data.start_date}</strong> to <strong>{data.end_date}</strong>
            </div>
          )}
          <button
            onClick={loadData}
            disabled={loading}
            className="btn secondary small"
            style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
          >
            {loading ? "Refreshing..." : "↻ Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div
          style={{
            backgroundColor: "#fef2f2",
            border: "1px solid #fca5a5",
            borderRadius: "8px",
            padding: "16px",
            marginBottom: "20px",
            color: "#991b1b",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div>
            <strong>Error loading placement health:</strong> {error}
          </div>
          <button onClick={loadData} className="btn secondary small">
            Retry
          </button>
        </div>
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
        <div className="card" style={{ margin: 0, padding: "16px" }}>
          <div className="muted" style={{ fontSize: "12px", textTransform: "uppercase", fontWeight: 600 }}>
            Total Placements
          </div>
          <div style={{ fontSize: "28px", fontWeight: 700, marginTop: "6px" }}>
            {data ? num(data.total_placements) : "—"}
          </div>
          <div className="muted" style={{ fontSize: "12px", marginTop: "4px" }}>
            Active inventory slots
          </div>
        </div>

        <div className="card" style={{ margin: 0, padding: "16px", borderLeft: "4px solid #10b981" }}>
          <div style={{ color: "#15803d", fontSize: "12px", textTransform: "uppercase", fontWeight: 600 }}>
            Great (CTR &gt; 1.0%)
          </div>
          <div style={{ fontSize: "28px", fontWeight: 700, marginTop: "6px", color: "#15803d" }}>
            {data ? num(countGreat) : "—"}
          </div>
          <div className="muted" style={{ fontSize: "12px", marginTop: "4px" }}>
            High-yield inventory
          </div>
        </div>

        <div className="card" style={{ margin: 0, padding: "16px", borderLeft: "4px solid #3b82f6" }}>
          <div style={{ color: "#1d4ed8", fontSize: "12px", textTransform: "uppercase", fontWeight: 600 }}>
            Healthy (0.2% - 1.0%)
          </div>
          <div style={{ fontSize: "28px", fontWeight: 700, marginTop: "6px", color: "#1d4ed8" }}>
            {data ? num(countHealthy) : "—"}
          </div>
          <div className="muted" style={{ fontSize: "12px", marginTop: "4px" }}>
            Consistent performers
          </div>
        </div>

        <div className="card" style={{ margin: 0, padding: "16px", borderLeft: "4px solid #f59e0b" }}>
          <div style={{ color: "#b45309", fontSize: "12px", textTransform: "uppercase", fontWeight: 600 }}>
            Low Visibility (&lt; 0.2%)
          </div>
          <div style={{ fontSize: "28px", fontWeight: 700, marginTop: "6px", color: "#b45309" }}>
            {data ? num(countLowVis) : "—"}
          </div>
          <div className="muted" style={{ fontSize: "12px", marginTop: "4px" }}>
            Needs position adjustment
          </div>
        </div>

        <div className="card" style={{ margin: 0, padding: "16px", borderLeft: "4px solid #94a3b8" }}>
          <div style={{ color: "#475569", fontSize: "12px", textTransform: "uppercase", fontWeight: 600 }}>
            Insufficient Data (&lt; 100)
          </div>
          <div style={{ fontSize: "28px", fontWeight: 700, marginTop: "6px", color: "#475569" }}>
            {data ? num(countInsufficient) : "—"}
          </div>
          <div className="muted" style={{ fontSize: "12px", marginTop: "4px" }}>
            Awaiting 100+ impressions
          </div>
        </div>
      </div>

      {/* Platform Benchmarks Reference Card */}
      {data && data.benchmarks && Object.keys(data.benchmarks).length > 0 && (
        <div
          className="card"
          style={{
            marginBottom: "24px",
            backgroundColor: "#f8fafc",
            border: "1px solid #e2e8f0",
            padding: "16px 20px",
          }}
        >
          <div
            className="flex row space-between align-center"
            style={{ marginBottom: "12px", flexWrap: "wrap", gap: "8px" }}
          >
            <div>
              <h3 style={{ margin: 0, fontSize: "15px", fontWeight: 600, color: "#1e293b" }}>
                📊 Platform Benchmark Medians (Format-Specific)
              </h3>
              <p className="muted" style={{ margin: "2px 0 0", fontSize: "12px" }}>
                Computed from eligible platform placements (minimum 100 impressions). Comparisons are strictly format-to-format.
              </p>
            </div>
            <span
              style={{
                fontSize: "11px",
                color: "#64748b",
                backgroundColor: "#e2e8f0",
                padding: "2px 8px",
                borderRadius: "4px",
              }}
            >
              Privacy Safe • Zero Competitor Leakage
            </span>
          </div>

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
                  backgroundColor: "#ffffff",
                  border: "1px solid #cbd5e1",
                  borderRadius: "8px",
                  padding: "12px 14px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <strong>{bm.format_name || fmtKey}</strong>
                  <span className="muted" style={{ fontSize: "11px" }}>
                    {bm.eligible_slots_count} eligible slots
                  </span>
                </div>
                <div style={{ display: "flex", gap: "16px", marginTop: "8px", fontSize: "13px" }}>
                  <div>
                    <span className="muted" style={{ fontSize: "11px", display: "block" }}>
                      Median CTR
                    </span>
                    <strong>{bm.median_ctr !== null ? (bm.median_ctr * 100).toFixed(2) + "%" : "N/A"}</strong>
                  </div>
                  <div>
                    <span className="muted" style={{ fontSize: "11px", display: "block" }}>
                      Median eCPM
                    </span>
                    <strong>{bm.median_ecpm !== null ? money(bm.median_ecpm) : "N/A"}</strong>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Main Placement Health Table Card */}
      <div className="card">
        {/* Table Filters and Search */}
        <div
          className="flex row space-between align-center"
          style={{
            marginBottom: "16px",
            paddingBottom: "16px",
            borderBottom: "1px solid #e2e8f0",
            flexWrap: "wrap",
            gap: "12px",
          }}
        >
          {/* Verdict Filter Buttons */}
          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
            {(
              [
                { id: "ALL", label: `All (${placements.length})` },
                { id: "great", label: `Great (${countGreat})` },
                { id: "healthy", label: `Healthy (${countHealthy})` },
                { id: "low_visibility", label: `Low Vis (${countLowVis})` },
                { id: "insufficient_data", label: `Insufficient (${countInsufficient})` },
              ] as const
            ).map((btn) => (
              <button
                key={btn.id}
                onClick={() => setFilter(btn.id)}
                className={`btn small ${filter === btn.id ? "primary" : "secondary"}`}
                style={{ fontSize: "12px" }}
              >
                {btn.label}
              </button>
            ))}
          </div>

          {/* Search box */}
          <div style={{ minWidth: "220px" }}>
            <input
              type="text"
              placeholder="Search placement or code..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="input"
              style={{
                width: "100%",
                padding: "6px 12px",
                fontSize: "13px",
                borderRadius: "6px",
                border: "1px solid #cbd5e1",
              }}
            />
          </div>
        </div>

        {/* Loading State */}
        {loading && !data && (
          <div style={{ padding: "48px", textAlign: "center" }} className="muted">
            <div style={{ fontSize: "18px", marginBottom: "8px" }}>⏳</div>
            Loading placement health analysis...
          </div>
        )}

        {/* Empty Inventory State */}
        {!loading && placements.length === 0 && (
          <div style={{ padding: "48px 24px", textAlign: "center" }}>
            <div style={{ fontSize: "36px", marginBottom: "12px" }}>📦</div>
            <h3 style={{ margin: "0 0 8px" }}>No Ad Placements Found</h3>
            <p className="muted" style={{ maxWidth: "480px", margin: "0 auto 20px", fontSize: "14px" }}>
              You haven't configured any ad placements yet. Create your first ad unit to begin tracking impressions and receiving automated performance health verdicts.
            </p>
            <Link to="/publisher/slots" className="btn primary">
              Create Ad Unit →
            </Link>
          </div>
        )}

        {/* Filtered Empty State */}
        {!loading && placements.length > 0 && filteredPlacements.length === 0 && (
          <div style={{ padding: "36px", textAlign: "center" }} className="muted">
            No placements matched the selected filter (<strong>{filter}</strong>).
          </div>
        )}

        {/* Placements Table */}
        {!loading && filteredPlacements.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table className="table" style={{ width: "100%" }}>
              <thead>
                <tr>
                  <th style={{ width: "24%" }}>Placement</th>
                  <th style={{ width: "10%" }}>Format</th>
                  <th style={{ width: "12%", textAlign: "right" }}>30d Volume</th>
                  <th style={{ width: "13%", textAlign: "right" }}>CTR &amp; Peer</th>
                  <th style={{ width: "13%", textAlign: "right" }}>eCPM &amp; Peer</th>
                  <th style={{ width: "12%" }}>Health Verdict</th>
                  <th style={{ width: "16%" }}>Recommended Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredPlacements.map((p) => {
                  const bm = p.benchmark;
                  const hasBenchmark = bm && bm.median_ctr !== null && bm.median_ecpm !== null;
                  const ctrDiff = hasBenchmark ? p.ctr - (bm.median_ctr || 0) : null;
                  const ecpmDiff = hasBenchmark ? p.ecpm - (bm.median_ecpm || 0) : null;

                  return (
                    <tr key={p.slot_id}>
                      {/* Name & ID */}
                      <td>
                        <div style={{ fontWeight: 600, color: "#0f172a" }}>{p.slot_name}</div>
                        <div
                          style={{
                            fontSize: "11px",
                            fontFamily: "monospace",
                            color: "#64748b",
                            marginTop: "2px",
                          }}
                        >
                          {p.slot_id}
                        </div>
                      </td>

                      {/* Format */}
                      <td>{renderFormatBadge(p.format_name)}</td>

                      {/* Volume (Impressions & Clicks) */}
                      <td style={{ textAlign: "right" }}>
                        <div style={{ fontWeight: 600 }}>{num(p.impressions)} impr</div>
                        <div className="muted" style={{ fontSize: "11px", marginTop: "2px" }}>
                          {num(p.clicks)} clicks • {money(p.earnings)}
                        </div>
                      </td>

                      {/* CTR vs Peer Benchmark */}
                      <td style={{ textAlign: "right" }}>
                        <div style={{ fontWeight: 600 }}>{(p.ctr * 100).toFixed(2)}%</div>
                        {hasBenchmark ? (
                          <div
                            style={{
                              fontSize: "11px",
                              marginTop: "2px",
                              color: (ctrDiff || 0) >= 0 ? "#15803d" : "#b45309",
                            }}
                          >
                            {(ctrDiff || 0) >= 0 ? "+" : ""}
                            {((ctrDiff || 0) * 100).toFixed(2)}% vs med ({((bm.median_ctr || 0) * 100).toFixed(2)}%)
                          </div>
                        ) : (
                          <div className="muted" style={{ fontSize: "11px", marginTop: "2px" }}>
                            no peer benchmark
                          </div>
                        )}
                      </td>

                      {/* eCPM vs Peer Benchmark */}
                      <td style={{ textAlign: "right" }}>
                        <div style={{ fontWeight: 600 }}>{money(p.ecpm)}</div>
                        {hasBenchmark ? (
                          <div
                            style={{
                              fontSize: "11px",
                              marginTop: "2px",
                              color: (ecpmDiff || 0) >= 0 ? "#15803d" : "#b45309",
                            }}
                          >
                            {(ecpmDiff || 0) >= 0 ? "+" : ""}
                            {money(ecpmDiff || 0)} vs med ({money(bm.median_ecpm || 0)})
                          </div>
                        ) : (
                          <div className="muted" style={{ fontSize: "11px", marginTop: "2px" }}>
                            no peer benchmark
                          </div>
                        )}
                      </td>

                      {/* Health Verdict */}
                      <td>{renderVerdictBadge(p.verdict)}</td>

                      {/* Recommendation */}
                      <td>
                        <div
                          style={{
                            fontSize: "12px",
                            fontWeight: 500,
                            color:
                              p.verdict === "great"
                                ? "#15803d"
                                : p.verdict === "low_visibility"
                                ? "#b45309"
                                : p.verdict === "insufficient_data"
                                ? "#64748b"
                                : "#1e293b",
                            lineHeight: 1.4,
                          }}
                        >
                          {p.recommendation}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Helpful Guidance Footer Card */}
      <div
        className="card"
        style={{
          marginTop: "24px",
          backgroundColor: "#f8fafc",
          border: "1px solid #e2e8f0",
          fontSize: "13px",
          lineHeight: 1.6,
          color: "#475569",
        }}
      >
        <h4 style={{ margin: "0 0 8px", color: "#1e293b" }}>💡 Understanding Health Classifications</h4>
        <ul style={{ margin: 0, paddingLeft: "20px" }}>
          <li>
            <strong>Great (CTR &gt; 1.0%):</strong> Top-tier engagement. For CTR &gt; 2.0%, the optimizer recommends raising the floor price because advertiser demand exceeds available supply.
          </li>
          <li>
            <strong>Healthy (0.2% - 1.0% CTR):</strong> Standard, consistent performance aligning with healthy platform benchmarks.
          </li>
          <li>
            <strong>Low Visibility (CTR &lt; 0.2%):</strong> Placement engagement is below normal benchmarks. Recommended action is moving the slot above the fold or widening dimensions.
          </li>
          <li>
            <strong>Insufficient Data (&lt; 100 impressions):</strong> Guardrail threshold. Placements require at least 100 impressions within the 30-day window before receiving an automated health verdict.
          </li>
        </ul>
      </div>
    </div>
  );
}
