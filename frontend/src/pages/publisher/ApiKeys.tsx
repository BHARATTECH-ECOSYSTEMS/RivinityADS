import React, { useEffect, useState } from "react";
import { 
  getPublisherKeysUsage, 
  createPublisherApiKey, 
  revokePublisherApiKey, 
  type ApiKeyUsageResponse,
  type ApiKeyCreateResponse 
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { dateString } from "../../lib/format";
import {
  PageContainer,
  PageHeader,
  Card,
  Button,
  StatusBadge,
  Alert,
  LoadingSkeleton,
  EmptyState,
  ConfirmDialog
} from "../../components/ui";

export default function PublisherApiKeys() {
  const { session } = useAuth();
  const [keysUsage, setKeysUsage] = useState<ApiKeyUsageResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // Creation form state
  const [name, setName] = useState("");
  const [isSandbox, setIsSandbox] = useState(false);
  const [rateLimit, setRateLimit] = useState(60);
  const [dailyQuota, setDailyQuota] = useState(10000);
  const [creating, setCreating] = useState(false);
  const [newKeyData, setNewKeyData] = useState<ApiKeyCreateResponse | null>(null);
  const [selectedKey, setSelectedKey] = useState<ApiKeyUsageResponse | null>(null);

  // Revoke confirmation state
  const [keyToRevoke, setKeyToRevoke] = useState<ApiKeyUsageResponse | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [copiedKey, setCopiedKey] = useState(false);

  const publisherId = session?.user?.publisher_id;

  const loadData = async (isManualRefresh = false) => {
    if (!publisherId) return;
    try {
      if (isManualRefresh) setRefreshing(true);
      else setLoading(true);
      const data = await getPublisherKeysUsage(publisherId);
      setKeysUsage(data);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load API keys and usage");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => { 
    void loadData(); 
  }, [publisherId]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!publisherId) return;
    setCreating(true);
    setError(null);
    try {
      const res = await createPublisherApiKey(publisherId, {
        name: name.trim() || "Production API Key",
        is_sandbox: isSandbox,
        rate_limit_per_minute: Number(rateLimit) || 60,
        daily_quota: Number(dailyQuota) || 10000,
      });
      setNewKeyData(res);
      setName("");
      setIsSandbox(false);
      setRateLimit(60);
      setDailyQuota(10000);
      await loadData();
    } catch (err: any) {
      setError(err.message || "Failed to create API key");
    } finally {
      setCreating(false);
    }
  };

  const confirmRevoke = async () => {
    if (!publisherId || !keyToRevoke) return;
    setRevoking(true);
    try {
      await revokePublisherApiKey(publisherId, keyToRevoke.key_id);
      setKeyToRevoke(null);
      if (selectedKey && selectedKey.key_id === keyToRevoke.key_id) {
        setSelectedKey(null);
      }
      await loadData();
    } catch (err: any) {
      setError(err.message || "Failed to revoke API key");
    } finally {
      setRevoking(false);
    }
  };

  const copyKey = async () => {
    if (newKeyData) {
      try {
        await navigator.clipboard.writeText(newKeyData.raw_key);
        setCopiedKey(true);
        setTimeout(() => setCopiedKey(false), 2500);
      } catch {
        // Clipboard failed
      }
    }
  };

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Security & Credentials"
        title="API Keys & Quotas"
        description="Manage server-to-server cryptographic API credentials, track real-time rate limits, and inspect quotas."
        actions={
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void loadData(true)}
            isLoading={refreshing}
          >
            Refresh Usage
          </Button>
        }
      />

      {error && (
        <Alert variant="danger" title="API Key Error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* One-Time Raw Key Display Banner */}
      {newKeyData && (
        <div
          style={{
            border: "2px solid var(--color-success)",
            backgroundColor: "rgba(22, 163, 74, 0.06)",
            padding: "20px",
            borderRadius: "var(--radius-xl)",
            marginBottom: "24px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "8px" }}>
            <h3 style={{ margin: 0, color: "var(--color-success)", fontSize: "1.125rem", fontWeight: 700 }}>
              New API Key Generated Successfully
            </h3>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setNewKeyData(null)}
              style={{ padding: "2px 6px" }}
            >
              ✕ Done
            </button>
          </div>
          <p style={{ fontSize: "0.875rem", color: "var(--color-text-secondary)", marginBottom: "16px" }}>
            <strong>CRITICAL SECURITY NOTICE:</strong> This raw cryptographic secret is displayed <strong>exactly once</strong>. It cannot be recovered later. Store it safely in your backend environment variables.
          </p>
          <div style={{ display: "flex", gap: "10px", alignItems: "center", marginBottom: "12px" }}>
            <input 
              type="text" 
              readOnly 
              value={newKeyData.raw_key} 
              className="input" 
              style={{ flex: 1, fontFamily: "var(--font-mono)", backgroundColor: "var(--color-bg-surface)", fontWeight: 600 }} 
            />
            <Button variant="primary" onClick={copyKey}>
              {copiedKey ? "Copied Secret!" : "Copy Secret"}
            </Button>
            <Button variant="secondary" onClick={() => setNewKeyData(null)}>
              I Have Saved This Key
            </Button>
          </div>
          <div style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
            Environment: <strong>{newKeyData.is_sandbox ? "Sandbox" : "Production Live"}</strong> | Rate Limit: <strong>{newKeyData.rate_limit_per_minute} req/min</strong> | Daily Quota: <strong>{newKeyData.daily_quota.toLocaleString()} req/day</strong>
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "2.3fr 1.7fr", gap: "24px", alignItems: "start" }}>
        
        {/* API Keys Inventory Table */}
        <Card
          title="Active Credentials &amp; Quotas"
          subtitle={`${keysUsage.length} keys registered for server-to-server endpoints.`}
        >
          {loading ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px", padding: "16px 0" }}>
              <LoadingSkeleton variant="rect" height={50} />
              <LoadingSkeleton variant="rect" height={50} />
            </div>
          ) : keysUsage.length === 0 ? (
            <EmptyState
              title="No API keys configured"
              description="Create a server key to integrate with automated bidding or conversational backend pipelines."
            />
          ) : (
            <div className="table-container" style={{ overflowX: "auto" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Key Name &amp; Prefix</th>
                    <th>Rate Limit</th>
                    <th>Daily Quota</th>
                    <th>Status</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {keysUsage.map((k) => {
                    const isRevoked = k.status === 0 || !!k.revoked_at;
                    const quotaPercent = k.daily_quota > 0 ? Math.min(100, Math.round((k.today_usage / k.daily_quota) * 100)) : 0;

                    return (
                      <tr key={k.key_id}>
                        <td>
                          <div style={{ fontWeight: 600 }}>{k.name}</div>
                          <div style={{ fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--color-text-muted)" }}>
                            {k.key_prefix}...
                          </div>
                          <div style={{ marginTop: "4px" }}>
                            <span style={{ fontSize: "10px", padding: "1px 6px", borderRadius: "var(--radius-sm)", background: k.is_sandbox ? "#fef3c7" : "#dbeafe", color: k.is_sandbox ? "#b45309" : "#1e40af", fontWeight: 600 }}>
                              {k.is_sandbox ? "SANDBOX" : "PRODUCTION"}
                            </span>
                          </div>
                        </td>
                        <td>
                          <div style={{ fontSize: "0.8125rem", fontWeight: 600 }}>
                            {k.current_minute_usage} / {k.rate_limit_per_minute} req/min
                          </div>
                          <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                            {k.rate_limit_remaining} remaining
                          </div>
                        </td>
                        <td>
                          <div style={{ fontSize: "0.8125rem", fontWeight: 600 }}>
                            {k.today_usage.toLocaleString()} / {k.daily_quota.toLocaleString()}
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
                                width: `${quotaPercent}%`,
                                backgroundColor: quotaPercent > 90 ? "var(--color-danger)" : "var(--color-primary)",
                              }}
                            />
                          </div>
                        </td>
                        <td>
                          <StatusBadge 
                            status={isRevoked ? "failed" : "active"} 
                            label={isRevoked ? "Revoked" : "Active"} 
                            size="sm" 
                          />
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <div style={{ display: "flex", gap: "6px", justifyContent: "flex-end" }}>
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => setSelectedKey(k)}
                            >
                              Metrics
                            </Button>
                            {!isRevoked && (
                              <Button
                                variant="danger"
                                size="sm"
                                onClick={() => setKeyToRevoke(k)}
                              >
                                Revoke
                              </Button>
                            )}
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

        {/* Generate API Key Form */}
        <Card
          title="Generate New API Key"
          subtitle="Configure request quotas and authentication environment."
        >
          <form onSubmit={handleCreate}>
            <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <div className="field">
                <label htmlFor="key-name" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                  Key Description / Label *
                </label>
                <input
                  id="key-name"
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="input"
                  placeholder="e.g. Chatbot S2S Ad Integration"
                  disabled={creating}
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div className="field">
                  <label htmlFor="rate-limit" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                    Rate Limit (req/min)
                  </label>
                  <input
                    id="rate-limit"
                    type="number"
                    min="1"
                    max="10000"
                    value={rateLimit}
                    onChange={(e) => setRateLimit(Number(e.target.value))}
                    className="input"
                    disabled={creating}
                  />
                </div>
                <div className="field">
                  <label htmlFor="daily-quota" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                    Daily Quota (req/day)
                  </label>
                  <input
                    id="daily-quota"
                    type="number"
                    min="100"
                    max="1000000"
                    value={dailyQuota}
                    onChange={(e) => setDailyQuota(Number(e.target.value))}
                    className="input"
                    disabled={creating}
                  />
                </div>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "4px" }}>
                <input
                  id="sandbox-mode"
                  type="checkbox"
                  checked={isSandbox}
                  onChange={(e) => setIsSandbox(e.target.checked)}
                  disabled={creating}
                />
                <label htmlFor="sandbox-mode" style={{ fontSize: "0.875rem", cursor: "pointer" }}>
                  Sandbox Key (test ads only, no billing debits)
                </label>
              </div>

              <div style={{ marginTop: "12px" }}>
                <Button 
                  type="submit" 
                  variant="primary" 
                  isLoading={creating} 
                  disabled={!name.trim()}
                  style={{ width: "100%" }}
                >
                  Generate Key Pair
                </Button>
              </div>
            </div>
          </form>
        </Card>
      </div>

      {/* Usage Metrics Modal */}
      {selectedKey && (
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
              maxWidth: "550px",
              width: "100%",
              padding: "24px",
              boxShadow: "var(--shadow-xl)",
              border: "1px solid var(--border-color)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
              <div>
                <h3 style={{ margin: 0, fontSize: "1.125rem", fontWeight: 700 }}>
                  Usage Metrics: {selectedKey.name}
                </h3>
                <p style={{ margin: "4px 0 0", fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                  Key Prefix: {selectedKey.key_prefix}...
                </p>
              </div>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setSelectedKey(null)}
                aria-label="Close dialog"
              >
                ✕
              </button>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "20px" }}>
              <div style={{ background: "var(--color-bg-subtle)", padding: "12px", borderRadius: "var(--radius-md)" }}>
                <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>Total Today Requests</div>
                <div style={{ fontSize: "1.25rem", fontWeight: 700, marginTop: "2px" }}>
                  {selectedKey.breakdown.total_requests.toLocaleString()}
                </div>
              </div>
              <div style={{ background: "var(--color-bg-subtle)", padding: "12px", borderRadius: "var(--radius-md)" }}>
                <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>Ads Served</div>
                <div style={{ fontSize: "1.25rem", fontWeight: 700, marginTop: "2px", color: "var(--color-success)" }}>
                  {selectedKey.breakdown.served_ads.toLocaleString()}
                </div>
              </div>
              <div style={{ background: "var(--color-bg-subtle)", padding: "12px", borderRadius: "var(--radius-md)" }}>
                <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>Rate Limited Requests</div>
                <div style={{ fontSize: "1.25rem", fontWeight: 700, marginTop: "2px", color: selectedKey.breakdown.rate_limited_requests > 0 ? "var(--color-danger)" : "inherit" }}>
                  {selectedKey.breakdown.rate_limited_requests.toLocaleString()}
                </div>
              </div>
              <div style={{ background: "var(--color-bg-subtle)", padding: "12px", borderRadius: "var(--radius-md)" }}>
                <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>Quota Exceeded</div>
                <div style={{ fontSize: "1.25rem", fontWeight: 700, marginTop: "2px" }}>
                  {selectedKey.breakdown.quota_exceeded_requests.toLocaleString()}
                </div>
              </div>
            </div>

            <div style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)", marginBottom: "20px" }}>
              Created: <strong>{dateString(selectedKey.created_at)}</strong> | Last Used: <strong>{selectedKey.last_used_at ? dateString(selectedKey.last_used_at) : "Never"}</strong>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <Button variant="secondary" onClick={() => setSelectedKey(null)}>
                Close
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Revocation Confirmation Dialog */}
      <ConfirmDialog
        isOpen={!!keyToRevoke}
        title="Revoke API Key"
        message={`Are you sure you want to revoke key "${keyToRevoke?.name}" (${keyToRevoke?.key_prefix}...)? Any integrations using this secret key will immediately be rejected by the server.`}
        confirmText="Revoke Key"
        variant="danger"
        isLoading={revoking}
        onConfirm={confirmRevoke}
        onCancel={() => setKeyToRevoke(null)}
      />
    </PageContainer>
  );
}
