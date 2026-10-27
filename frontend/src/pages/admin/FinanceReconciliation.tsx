import React, { useEffect, useState } from "react";
import { 
  listAdvertisers, 
  listPublishers, 
  getAdvertiserLedger, 
  getPublisherLedger,
  type LedgerResponse,
  type TransactionResponse
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
  const [filterType, setFilterType] = useState<string>("ALL");
  const [searchRef, setSearchRef] = useState<string>("");

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

  const selectedEntityName = targetType === "advertiser"
    ? advertisers.find((a) => a.id === selectedId)?.name
    : publishers.find((p) => p.id === selectedId)?.name;

  const transactions = ledger?.transactions || [];
  const filteredTransactions = transactions.filter((tx) => {
    if (filterType !== "ALL") {
      if (filterType === "deposit" && tx.type !== 1) return false;
      if (filterType === "spend" && tx.type !== 2) return false;
      if (filterType === "payout" && tx.type !== 3) return false;
      if (filterType === "earnings" && tx.type !== 4) return false;
    }
    if (searchRef.trim()) {
      const q = searchRef.toLowerCase();
      return (
        String(tx.id).includes(q) ||
        (tx.reference_id && tx.reference_id.toLowerCase().includes(q)) ||
        (tx.description && tx.description.toLowerCase().includes(q))
      );
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
        return "Earnings";
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

      {/* Target Selector Card */}
      <Card
        title="Select Reconciliation Account"
        subtitle="Choose whether to audit an Advertiser wallet or a Publisher earnings ledger."
        style={{ marginBottom: "24px" }}
      >
        <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: "16px", alignItems: "center" }}>
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

      {/* Ledger Overview & Transactions */}
      {selectedId && (
        <Card
          title={`Ledger Audit: ${selectedEntityName || `Account #${selectedId}`}`}
          subtitle={`Immutable double-entry transaction history stored in PostgreSQL.`}
          footer={
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
              <span style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                Audited Balance: <strong>{ledger ? money(ledger.balance) : "..."}</strong>
              </span>
              <span style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                Displaying up to 100 most recent transactions.
              </span>
            </div>
          }
        >
          {/* Controls: Filter and Search */}
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
            <div style={{ display: "flex", gap: "6px" }}>
              {["ALL", "deposit", "spend", "payout", "earnings"].map((t) => (
                <button
                  key={t}
                  type="button"
                  className={`btn ${filterType === t ? "btn-primary" : "btn-secondary"} btn-sm`}
                  onClick={() => setFilterType(t)}
                  style={{ textTransform: "capitalize" }}
                >
                  {t}
                </button>
              ))}
            </div>

            <div style={{ minWidth: "220px" }}>
              <input
                type="text"
                placeholder="Search reference or ID..."
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
                    <th>Reference / External ID</th>
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
                              background: tx.type === 1 || tx.type === 4 ? "#dcfce7" : "#fee2e2",
                              color: tx.type === 1 || tx.type === 4 ? "#15803d" : "#b91c1c",
                            }}
                          >
                            {getTransactionTypeLabel(tx.type)}
                          </span>
                        </td>
                        <td style={{ fontWeight: 700, fontSize: "0.9375rem", color: isCredit ? "var(--color-success)" : "inherit" }}>
                          {isCredit ? "+" : ""}{money(tx.amount)}
                        </td>
                        <td>
                          {tx.reference_id ? (
                            <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.8125rem", color: "var(--color-text-secondary)" }}>
                              {tx.reference_id}
                            </code>
                          ) : (
                            <span style={{ color: "var(--color-text-muted)" }}>—</span>
                          )}
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
