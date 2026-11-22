import React, { useEffect, useState } from "react";
import { 
  listAdvertisers, 
  listPublishers, 
  getAdvertiserLedger, 
  getPublisherLedger,
  runReconciliation,
  type LedgerResponse,
  type TransactionResponse,
  type ReconciliationReport,
  type UnresolvedDiscrepancy,
} from "../../api/client";
import type { Advertiser, Publisher } from "../../types";
import { money, dateString } from "../../lib/format";
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

export default function FinanceReconciliation() {
  const [advertisers, setAdvertisers] = useState<Advertiser[]>([]);
  const [publishers, setPublishers] = useState<Publisher[]>([]);
  const [loadingEntities, setLoadingEntities] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Selected reconciliation target
  const [targetType, setTargetType] = useState<"advertiser" | "publisher">("advertiser");
  const [selectedId, setSelectedId] = useState<number | "">("");
  const [ledger, setLedger] = useState<LedgerResponse | null>(null);
  const [loadingLedger, setLoadingLedger] = useState(false);

  // Filters
  const [filterType, setFilterType] = useState<string>("ALL");
  const [filterCleared, setFilterCleared] = useState<string>("ALL");
  const [searchRef, setSearchRef] = useState<string>("");
  const [startDate, setStartDate] = useState<string>("");
  const [endDate, setEndDate] = useState<string>("");

  // Reconciliation Audit Report
  const [auditReport, setAuditReport] = useState<ReconciliationReport | null>(null);
  const [runningAudit, setRunningAudit] = useState(false);

  useEffect(() => {
    setLoadingEntities(true);
    Promise.all([listAdvertisers(), listPublishers()])
      .then(([advs, pubs]) => {
        setAdvertisers(advs);
        setPublishers(pubs);
        if (advs.length > 0) {
          setSelectedId(advs[0].id);
        }
      })
      .catch((err) => {
        setError(err.message || "Failed to load account entities");
      })
      .finally(() => setLoadingEntities(false));
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setLedger(null);
      return;
    }
    setLoadingLedger(true);
    setError(null);
    const fetchLedger = targetType === "advertiser" 
      ? getAdvertiserLedger(Number(selectedId)) 
      : getPublisherLedger(Number(selectedId));

    fetchLedger
      .then(setLedger)
      .catch((err) => {
        setError(err.message || "Failed to load ledger transactions");
        setLedger(null);
      })
      .finally(() => setLoadingLedger(false));
  }, [targetType, selectedId]);

  const handleRunReconciliation = () => {
    setRunningAudit(true);
    setError(null);
    runReconciliation()
      .then(setAuditReport)
      .catch((err) => {
        setError(err.message || "Failed to run authoritative financial reconciliation");
      })
      .finally(() => setRunningAudit(false));
  };

  const selectedEntityName = targetType === "advertiser"
    ? advertisers.find((a) => a.id === selectedId)?.name
    : publishers.find((p) => p.id === selectedId)?.name;

  const transactions = ledger?.transactions || [];
  const filteredTransactions = transactions.filter((tx) => {
    // Type filter
    if (filterType !== "ALL") {
      if (filterType === "deposit" && tx.type !== 1) return false;
      if (filterType === "spend" && tx.type !== 2) return false;
      if (filterType === "payout" && tx.type !== 3) return false;
      if (filterType === "refund" && tx.type !== 4) return false;
      if (filterType === "earnings" && tx.type !== 5 && tx.type !== 4) return false;
      if (filterType === "adjustment" && tx.type !== 6) return false;
    }
    // Cleared / hold filter
    if (filterCleared !== "ALL") {
      if (filterCleared === "cleared" && tx.is_cleared === false) return false;
      if (filterCleared === "hold" && tx.is_cleared !== false) return false;
    }
    // Date range filter
    if (startDate) {
      const txDate = tx.created_at.split("T")[0];
      if (txDate < startDate) return false;
    }
    if (endDate) {
      const txDate = tx.created_at.split("T")[0];
      if (txDate > endDate) return false;
    }
    // Reference search filter
    if (searchRef.trim()) {
      const q = searchRef.toLowerCase();
      const matchId = String(tx.id).includes(q);
      const matchRef = tx.reference_id && tx.reference_id.toLowerCase().includes(q);
      const matchProv = tx.provider_event_id && tx.provider_event_id.toLowerCase().includes(q);
      const matchDesc = tx.description && tx.description.toLowerCase().includes(q);
      if (!matchId && !matchRef && !matchProv && !matchDesc) return false;
    }
    return true;
  });

  const getTransactionTypeLabel = (type: number) => {
    switch (type) {
      case 1:
        return "Deposit";
      case 2:
        return "Ad Spend";
      case 3:
        return "Payout";
      case 4:
        return "Refund";
      case 5:
        return "Earnings";
      case 6:
        return "Adjustment";
      default:
        return `Type #${type}`;
    }
  };

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Platform Operations"
        title="Financial & Payment Reconciliation"
        description="Inspect auditable immutable ledger records, verify deposits/payouts, and reconcile tenant financial balances."
      />

      {error && (
        <Alert variant="danger" title="Reconciliation Notice" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Target Selector & Reconciliation Run Card */}
      <Card
        title="Select Reconciliation Account"
        subtitle="Trigger cross-system telemetry audit or audit an individual Advertiser wallet / Publisher ledger."
        style={{ marginBottom: "24px" }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "16px", marginBottom: "20px" }}>
          <div>
            <span style={{ fontSize: "0.875rem", color: "var(--color-text-secondary)" }}>
              Run authoritative audit comparing delivery events, immutable transactions, and balances:
            </span>
          </div>
          <Button
            variant="primary"
            onClick={handleRunReconciliation}
            disabled={runningAudit}
          >
            {runningAudit ? "Auditing Pipeline..." : "Audit System Telemetry vs Ledger"}
          </Button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: "16px", alignItems: "center", borderTop: "1px solid var(--color-border)", paddingTop: "16px" }}>
          <div>
            <label style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "6px", display: "block" }}>
              Account Type:
            </label>
            <div style={{ display: "flex", gap: "8px" }}>
              <button
                type="button"
                className={`btn ${targetType === "advertiser" ? "btn-primary" : "btn-secondary"} btn-sm`}
                onClick={() => {
                  setTargetType("advertiser");
                  if (advertisers.length > 0) setSelectedId(advertisers[0].id);
                }}
              >
                Advertiser Wallet
              </button>
              <button
                type="button"
                className={`btn ${targetType === "publisher" ? "btn-primary" : "btn-secondary"} btn-sm`}
                onClick={() => {
                  setTargetType("publisher");
                  if (publishers.length > 0) setSelectedId(publishers[0].id);
                }}
              >
                Publisher Ledger
              </button>
            </div>
          </div>

          <div>
            <label htmlFor="entity-select" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "6px", display: "block" }}>
              Target Tenant Account:
            </label>
            {loadingEntities ? (
              <LoadingSkeleton variant="rect" height={38} />
            ) : (
              <select
                id="entity-select"
                value={selectedId}
                onChange={(e) => setSelectedId(e.target.value ? Number(e.target.value) : "")}
                className="select"
              >
                {targetType === "advertiser"
                  ? advertisers.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name} (ID #{a.id} · Balance: {money(a.balance)})
                      </option>
                    ))
                  : publishers.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name} (ID #{p.id} · {p.email || "No email"})
                      </option>
                    ))}
              </select>
            )}
          </div>
        </div>
      </Card>

      {/* System Reconciliation Audit Report (if run) */}
      {auditReport && (
        <Card
          title="Authoritative Reconciliation Results"
          subtitle={`Audit status: ${auditReport.status} · Period: ${auditReport.start_date} to ${auditReport.end_date}`}
          style={{ marginBottom: "24px" }}
        >
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "16px", marginBottom: "16px" }}>
            <div style={{ padding: "12px", background: "var(--color-bg-secondary)", borderRadius: "var(--radius-md)" }}>
              <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", textTransform: "uppercase" }}>Event Spend</div>
              <div style={{ fontSize: "1.125rem", fontWeight: 700 }}>{money(auditReport.total_event_spend)}</div>
            </div>
            <div style={{ padding: "12px", background: "var(--color-bg-secondary)", borderRadius: "var(--radius-md)" }}>
              <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", textTransform: "uppercase" }}>Ledger Debits</div>
              <div style={{ fontSize: "1.125rem", fontWeight: 700 }}>{money(auditReport.total_ledger_spend)}</div>
            </div>
            <div style={{ padding: "12px", background: "var(--color-bg-secondary)", borderRadius: "var(--radius-md)" }}>
              <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", textTransform: "uppercase" }}>Spend Drift</div>
              <div style={{ fontSize: "1.125rem", fontWeight: 700, color: auditReport.spend_delta > 0.01 ? "var(--color-danger)" : "var(--color-success)" }}>
                {money(auditReport.spend_delta)}
              </div>
            </div>
            <div style={{ padding: "12px", background: "var(--color-bg-secondary)", borderRadius: "var(--radius-md)" }}>
              <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", textTransform: "uppercase" }}>IVT Filtered</div>
              <div style={{ fontSize: "1.125rem", fontWeight: 700 }}>{auditReport.total_ivt_events} events</div>
            </div>
          </div>

          {/* Unresolved Queue */}
          <div style={{ marginTop: "16px" }}>
            <h4 style={{ fontSize: "0.875rem", fontWeight: 700, marginBottom: "8px" }}>
              Unresolved Discrepancies Queue ({auditReport.unresolved_queue.length})
            </h4>
            <div style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)", marginBottom: "12px" }}>
              Discrepancies are permanently queued for investigation. The platform never silently auto-balances ledgers.
            </div>

            {auditReport.unresolved_queue.length === 0 ? (
              <div style={{ padding: "12px", background: "#dcfce7", color: "#15803d", borderRadius: "var(--radius-sm)", fontSize: "0.875rem" }}>
                ✓ Zero discrepancies detected. Delivery events, ledger transactions, and wallet balances are strictly balanced.
              </div>
            ) : (
              <div className="table-container" style={{ overflowX: "auto" }}>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Queue ID</th>
                      <th>Severity</th>
                      <th>Entity</th>
                      <th>Metric</th>
                      <th>Expected</th>
                      <th>Actual</th>
                      <th>Delta</th>
                      <th>Reason / Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditReport.unresolved_queue.map((item) => (
                      <tr key={item.queue_id}>
                        <td><code>{item.queue_id}</code></td>
                        <td>
                          <span style={{
                            fontSize: "10px",
                            padding: "2px 6px",
                            borderRadius: "var(--radius-sm)",
                            fontWeight: 700,
                            background: item.severity === "CRITICAL" ? "#fee2e2" : "#fef3c7",
                            color: item.severity === "CRITICAL" ? "#b91c1c" : "#b45309",
                          }}>
                            {item.severity}
                          </span>
                        </td>
                        <td>{item.name}</td>
                        <td><code>{item.metric}</code></td>
                        <td>{money(item.expected_value)}</td>
                        <td>{money(item.actual_value)}</td>
                        <td style={{ fontWeight: 700, color: "var(--color-danger)" }}>{money(item.delta)}</td>
                        <td style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)" }}>{item.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Ledger Overview & Filter Controls */}
      {selectedId && (
        <Card
          title={`Ledger Audit: ${selectedEntityName || `Account #${selectedId}`}`}
          subtitle={`Immutable double-entry transaction history stored in PostgreSQL.`}
          footer={
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
              <span style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                Audited Available Balance: <strong>{ledger ? money(ledger.balance) : "..."}</strong>
              </span>
              <span style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                Displaying {filteredTransactions.length} of {transactions.length} transactions.
              </span>
            </div>
          }
        >
          {/* Controls: Multi-column Filter Bar */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "12px",
              marginBottom: "16px",
              paddingBottom: "16px",
              borderBottom: "1px solid var(--color-border)",
            }}
          >
            {/* Type buttons */}
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
              {["ALL", "deposit", "spend", "payout", "refund", "earnings", "adjustment"].map((t) => (
                <button
                  key={t}
                  type="button"
                  className={`btn ${filterType === t ? "btn-primary" : "btn-secondary"} btn-sm`}
                  onClick={() => setFilterType(t)}
                  style={{ textTransform: "capitalize", fontSize: "0.75rem", padding: "4px 8px" }}
                >
                  {t}
                </button>
              ))}
            </div>

            {/* Hold/Cleared status */}
            <div style={{ display: "flex", gap: "6px" }}>
              {["ALL", "cleared", "hold"].map((s) => (
                <button
                  key={s}
                  type="button"
                  className={`btn ${filterCleared === s ? "btn-primary" : "btn-secondary"} btn-sm`}
                  onClick={() => setFilterCleared(s)}
                  style={{ textTransform: "capitalize", fontSize: "0.75rem", padding: "4px 8px" }}
                >
                  {s === "hold" ? "In Hold" : s}
                </button>
              ))}
            </div>

            {/* Date range inputs */}
            <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="input"
                style={{ fontSize: "0.8125rem", padding: "4px 8px" }}
                title="Start Date"
              />
              <span style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>to</span>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="input"
                style={{ fontSize: "0.8125rem", padding: "4px 8px" }}
                title="End Date"
              />
              {(startDate || endDate) && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => { setStartDate(""); setEndDate(""); }}
                  style={{ fontSize: "0.75rem", padding: "4px 8px" }}
                >
                  Clear Dates
                </button>
              )}
            </div>

            {/* Search query input */}
            <div style={{ minWidth: "220px" }}>
              <input
                type="text"
                placeholder="Search reference, provider ID, or text..."
                value={searchRef}
                onChange={(e) => setSearchRef(e.target.value)}
                className="input"
                style={{ fontSize: "0.8125rem", padding: "6px 10px" }}
              />
            </div>
          </div>

          {loadingLedger ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px", padding: "16px 0" }}>
              <LoadingSkeleton variant="rect" height={40} />
              <LoadingSkeleton variant="rect" height={40} />
              <LoadingSkeleton variant="rect" height={40} />
            </div>
          ) : filteredTransactions.length === 0 ? (
            <EmptyState
              title="No transactions found"
              description="No financial ledger entries match the selected filter criteria."
            />
          ) : (
            <div className="table-container" style={{ overflowX: "auto" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Tx ID</th>
                    <th>Type</th>
                    <th>Amount</th>
                    <th>Reference &amp; Provider ID</th>
                    <th>Settlement Status</th>
                    <th>Rev Share</th>
                    <th>Description</th>
                    <th style={{ textAlign: "right" }}>Timestamp (UTC)</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredTransactions.map((tx) => {
                    const isCredit = tx.amount > 0;
                    return (
                      <tr key={tx.id}>
                        <td>
                          <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.8125rem", fontWeight: 600 }}>
                            #{tx.id}
                          </code>
                        </td>
                        <td>
                          <span
                            style={{
                              fontSize: "11px",
                              padding: "2px 8px",
                              borderRadius: "var(--radius-sm)",
                              fontWeight: 600,
                              background: tx.type === 1 || tx.type === 5 ? "#dcfce7" : tx.type === 4 ? "#e0e7ff" : "#fee2e2",
                              color: tx.type === 1 || tx.type === 5 ? "#15803d" : tx.type === 4 ? "#3730a3" : "#b91c1c",
                            }}
                          >
                            {getTransactionTypeLabel(tx.type)}
                          </span>
                        </td>
                        <td style={{ fontWeight: 700, fontSize: "0.9375rem", color: isCredit ? "var(--color-success)" : "inherit" }}>
                          {isCredit ? "+" : ""}{money(tx.amount)}
                        </td>
                        <td>
                          {tx.reference_id && (
                            <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem", display: "block", color: "var(--color-text-secondary)" }}>
                              {tx.reference_id}
                            </code>
                          )}
                          {tx.provider_event_id && (
                            <span style={{ fontSize: "0.6875rem", color: "var(--color-text-muted)", display: "block" }}>
                              Provider: {tx.provider_event_id}
                            </span>
                          )}
                          {!tx.reference_id && !tx.provider_event_id && (
                            <span style={{ color: "var(--color-text-muted)" }}>—</span>
                          )}
                        </td>
                        <td>
                          {tx.is_cleared === false ? (
                            <span style={{ fontSize: "11px", padding: "2px 6px", background: "#fef3c7", color: "#b45309", borderRadius: "var(--radius-sm)", fontWeight: 600 }}>
                              Estimated (Hold Window)
                            </span>
                          ) : (
                            <span style={{ fontSize: "11px", padding: "2px 6px", background: "#f1f5f9", color: "#475569", borderRadius: "var(--radius-sm)", fontWeight: 500 }}>
                              Finalised / Settled
                            </span>
                          )}
                        </td>
                        <td style={{ fontSize: "0.8125rem" }}>
                          {tx.rev_share_rate ? `${(Number(tx.rev_share_rate) * 100).toFixed(1)}%` : "—"}
                        </td>
                        <td style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)", maxWidth: "260px" }}>
                          {tx.description || "—"}
                        </td>
                        <td style={{ textAlign: "right", fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                          {dateString(tx.created_at)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </PageContainer>
  );
}
