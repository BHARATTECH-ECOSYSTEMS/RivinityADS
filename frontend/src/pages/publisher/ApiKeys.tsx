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

export default function PublisherApiKeys() {
  const { session } = useAuth();
  const [keysUsage, setKeysUsage] = useState<ApiKeyUsageResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  
  // Creation form state
  const [name, setName] = useState("");
  const [isSandbox, setIsSandbox] = useState(false);
  const [rateLimit, setRateLimit] = useState(60);
  const [dailyQuota, setDailyQuota] = useState(10000);
  const [newKeyData, setNewKeyData] = useState<ApiKeyCreateResponse | null>(null);
  const [selectedKey, setSelectedKey] = useState<ApiKeyUsageResponse | null>(null);

  const publisherId = session?.user?.publisher_id;

  const loadData = async (isManualRefresh = false) => {
    if (!publisherId) return;
    try {
      if (isManualRefresh) setRefreshing(true);
      else setLoading(true);
      const data = await getPublisherKeysUsage(publisherId);
      setKeysUsage(data);
      setError("");
    } catch (err: any) {
      setError(err.message || "Failed to load API keys and usage");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => { void loadData(); }, [publisherId]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!publisherId) return;
    try {
      const res = await createPublisherApiKey(publisherId, {
        name: name.trim() || "Default Key",
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
      alert(err.message || "Failed to create API key");
    }
  };

  const handleRevoke = async (keyId: number) => {
    if (!publisherId) return;
    if (!confirm("Are you sure you want to revoke this key? Any server integrations using it will immediately fail.")) return;
    try {
      await revokePublisherApiKey(publisherId, keyId);
      await loadData();
      if (selectedKey && selectedKey.key_id === keyId) {
        setSelectedKey(null);
      }
    } catch (err: any) {
      alert("Failed to revoke API key");
    }
  };

  const copyKey = () => {
    if (newKeyData) {
      navigator.clipboard.writeText(newKeyData.raw_key);
      alert("Copied raw API key to clipboard!");
    }
  };

  if (loading) return <div><p className="muted">Loading API Keys and Quotas...</p></div>;

  return (
    <div>
      <div className="flex row space-between align-center" style={{ marginBottom: "24px" }}>
        <div>
          <h1>API Keys & Usage Quotas</h1>
          <p className="muted">
            Manage cryptographic server-to-server API keys, track real-time rate limits, and monitor daily request quotas.
          </p>
        </div>
        <button 
          className="btn secondary" 
          onClick={() => void loadData(true)} 
          disabled={refreshing}
          style={{ display: "flex", alignItems: "center", gap: "8px" }}
        >
          {refreshing ? "Refreshing..." : "↻ Refresh Usage"}
        </button>
      </div>

      {error && (
        <div className="card" style={{ marginBottom: "24px", borderColor: "var(--error)" }}>
          <p className="error" style={{ marginBottom: "8px" }}>{error}</p>
          <button className="btn secondary small" onClick={() => void loadData()}>Retry</button>
        </div>
      )}

      {newKeyData && (
        <div className="card" style={{ marginBottom: "24px", border: "2px solid #10b981", backgroundColor: "rgba(16, 185, 129, 0.05)" }}>
          <h2 style={{ color: "#10b981", marginBottom: "8px" }}>API Key Generated Successfully</h2>
          <p className="muted" style={{ marginBottom: "16px" }}>
            <strong>CRITICAL SECURITY NOTICE:</strong> This raw key is shown <strong>exactly once</strong>. It cannot be recovered later. Store it safely in your environment variables.
          </p>
          <div className="flex row gap align-center" style={{ marginBottom: "12px" }}>
            <input 
              type="text" 
              readOnly 
              value={newKeyData.raw_key} 
              className="input" 
              style={{ flex: 1, fontFamily: "monospace", backgroundColor: "var(--bg-alt)" }} 
            />
            <button className="btn primary" onClick={copyKey}>Copy</button>
            <button className="btn secondary" onClick={() => setNewKeyData(null)}>Done</button>
          </div>
          <div style={{ fontSize: "0.85rem", color: "var(--muted)" }}>
            Environment: <strong>{newKeyData.is_sandbox ? "Sandbox" : "Production Live"}</strong> | Rate Limit: <strong>{newKeyData.rate_limit_per_minute} req/min</strong> | Daily Quota: <strong>{newKeyData.daily_quota.toLocaleString()} req/day</strong>
          </div>
        </div>
      )}

      {/* Usage Detail Modal */}
      {selectedKey && (
        <div 
          style={{
            position: "fixed",
            top: 0, left: 0, right: 0, bottom: 0,
            backgroundColor: "rgba(0,0,0,0.6)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: "20px"
          }}
          onClick={() => setSelectedKey(null)}
        >
          <div 
            className="card" 
            style={{ maxWidth: "560px", width: "100%", maxHeight: "90vh", overflowY: "auto" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex row space-between align-center" style={{ marginBottom: "16px" }}>
              <h2>Usage Detail: {selectedKey.name}</h2>
              <button className="btn secondary small" onClick={() => setSelectedKey(null)}>✕</button>
            </div>
            
            <div style={{ marginBottom: "16px", display: "flex", gap: "8px" }}>
              <span className="badge" style={{ backgroundColor: selectedKey.is_sandbox ? "#8b5cf6" : "#059669", color: "white" }}>
                {selectedKey.is_sandbox ? "Sandbox" : "Live"}
              </span>
              <span className="badge" style={{ backgroundColor: selectedKey.status === 1 ? "var(--success)" : "var(--error)", color: "white" }}>
                {selectedKey.status === 1 ? "Active" : "Revoked"}
              </span>
              <span style={{ fontFamily: "monospace", color: "var(--muted)", alignSelf: "center", fontSize: "0.85rem" }}>
                Prefix: {selectedKey.key_prefix}...
              </span>
            </div>

            <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "20px" }}>
              <div style={{ padding: "12px", border: "1px solid var(--border)", borderRadius: "6px" }}>
                <div style={{ fontSize: "0.8rem", color: "var(--muted)" }}>Per-Minute Rate Limit</div>
                <div style={{ fontSize: "1.2rem", fontWeight: 700 }}>{selectedKey.current_minute_usage} / {selectedKey.rate_limit_per_minute}</div>
                <div style={{ fontSize: "0.75rem", color: "var(--muted)" }}>Remaining: {selectedKey.rate_limit_remaining} reqs</div>
              </div>
              <div style={{ padding: "12px", border: "1px solid var(--border)", borderRadius: "6px" }}>
                <div style={{ fontSize: "0.8rem", color: "var(--muted)" }}>Daily Quota (UTC)</div>
                <div style={{ fontSize: "1.2rem", fontWeight: 700 }}>{selectedKey.today_usage.toLocaleString()} / {selectedKey.daily_quota.toLocaleString()}</div>
                <div style={{ fontSize: "0.75rem", color: "var(--muted)" }}>Remaining: {selectedKey.daily_quota_remaining.toLocaleString()} reqs</div>
              </div>
            </div>

            <h3 style={{ fontSize: "1rem", marginBottom: "8px" }}>Accounting Breakdown (Today UTC)</h3>
            <table className="table" style={{ marginBottom: "16px" }}>
              <tbody>
                <tr>
                  <td>Total Inbound Attempts</td>
                  <td style={{ textAlign: "right", fontWeight: 600 }}>{selectedKey.breakdown.total_requests.toLocaleString()}</td>
                </tr>
                <tr>
                  <td>Admitted Requests</td>
                  <td style={{ textAlign: "right", fontWeight: 600, color: "var(--success)" }}>{selectedKey.breakdown.admitted_requests.toLocaleString()}</td>
                </tr>
                <tr>
                  <td>Rate Limited (429)</td>
                  <td style={{ textAlign: "right", fontWeight: 600, color: selectedKey.breakdown.rate_limited_requests > 0 ? "var(--error)" : "inherit" }}>
                    {selectedKey.breakdown.rate_limited_requests.toLocaleString()}
                  </td>
                </tr>
                <tr>
                  <td>Daily Quota Exceeded (429)</td>
                  <td style={{ textAlign: "right", fontWeight: 600, color: selectedKey.breakdown.quota_exceeded_requests > 0 ? "var(--error)" : "inherit" }}>
                    {selectedKey.breakdown.quota_exceeded_requests.toLocaleString()}
                  </td>
                </tr>
                <tr>
                  <td>Delivered Ad Responses</td>
                  <td style={{ textAlign: "right", fontWeight: 600 }}>{selectedKey.breakdown.served_ads.toLocaleString()}</td>
                </tr>
              </tbody>
            </table>

            <div style={{ fontSize: "0.8rem", color: "var(--muted)", borderTop: "1px solid var(--border)", paddingTop: "12px" }}>
              <div>Quota Window: <strong>UTC Calendar Day (00:00 - 23:59 UTC)</strong></div>
              <div>Next Daily Reset: <strong>{selectedKey.daily_reset_time_utc}</strong> (in {Math.round(selectedKey.daily_reset_seconds / 3600)}h {Math.round((selectedKey.daily_reset_seconds % 3600) / 60)}m)</div>
              <div>Last Used: <strong>{selectedKey.last_used_at ? dateString(selectedKey.last_used_at) : "Never"}</strong></div>
            </div>
          </div>
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: "3fr 1fr", gap: "24px", alignItems: "start" }}>
        <div className="card">
          {keysUsage.length === 0 ? (
            <div style={{ padding: "48px 24px", textAlign: "center" }}>
              <p className="muted" style={{ marginBottom: "12px" }}>No API keys found. Generate a key to begin Server-to-Server integration.</p>
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Key & Type</th>
                    <th>Rate Limit</th>
                    <th>Daily Quota (Today)</th>
                    <th>Reset (UTC)</th>
                    <th>Status</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {keysUsage.map(k => {
                    const quotaPercent = Math.min(100, Math.round((k.today_usage / (k.daily_quota || 1)) * 100));
                    const isExhausted = k.today_usage >= k.daily_quota;
                    const isNearLimit = quotaPercent >= 80 && !isExhausted;

                    return (
                      <tr key={k.key_id}>
                        <td>
                          <div style={{ fontWeight: 600 }}>{k.name}</div>
                          <div style={{ display: "flex", gap: "6px", alignItems: "center", marginTop: "4px" }}>
                            <span 
                              style={{ 
                                fontSize: "0.7rem", 
                                padding: "2px 6px", 
                                borderRadius: "4px",
                                backgroundColor: k.is_sandbox ? "#8b5cf6" : "#059669",
                                color: "white" 
                              }}
                            >
                              {k.is_sandbox ? "SANDBOX" : "LIVE"}
                            </span>
                            <span style={{ fontFamily: "monospace", fontSize: "0.8rem", color: "var(--muted)" }}>
                              {k.key_prefix}...
                            </span>
                          </div>
                        </td>
                        <td>
                          <div><b>{k.rate_limit_per_minute}</b> / min</div>
                          <div style={{ fontSize: "0.75rem", color: "var(--muted)" }}>
                            {k.current_minute_usage} used now
                          </div>
                        </td>
                        <td style={{ minWidth: "160px" }}>
                          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85rem", marginBottom: "4px" }}>
                            <span><b>{k.today_usage.toLocaleString()}</b> / {k.daily_quota.toLocaleString()}</span>
                            <span>{quotaPercent}%</span>
                          </div>
                          <div style={{ width: "100%", height: "6px", backgroundColor: "var(--border)", borderRadius: "3px", overflow: "hidden" }}>
                            <div 
                              style={{ 
                                width: `${quotaPercent}%`, 
                                height: "100%", 
                                backgroundColor: isExhausted ? "var(--error)" : (isNearLimit ? "#f59e0b" : "var(--success)"),
                                transition: "width 0.3s ease"
                              }} 
                            />
                          </div>
                          {isExhausted && (
                            <span style={{ fontSize: "0.7rem", color: "var(--error)", fontWeight: 600 }}>QUOTA EXHAUSTED (429)</span>
                          )}
                          {isNearLimit && (
                            <span style={{ fontSize: "0.7rem", color: "#f59e0b", fontWeight: 600 }}>{k.daily_quota_remaining} remaining</span>
                          )}
                        </td>
                        <td>
                          <div style={{ fontSize: "0.85rem" }}>00:00 UTC</div>
                          <div style={{ fontSize: "0.75rem", color: "var(--muted)" }}>
                            in {Math.round(k.daily_reset_seconds / 3600)}h {Math.round((k.daily_reset_seconds % 3600) / 60)}m
                          </div>
                        </td>
                        <td>
                          {k.revoked_at ? (
                            <span className="badge" style={{ background: "var(--error)", color: "white" }}>Revoked</span>
                          ) : (
                            <span className="badge" style={{ background: "var(--success)", color: "white" }}>Active</span>
                          )}
                        </td>
                        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                          <button 
                            className="btn secondary small" 
                            style={{ marginRight: "6px" }}
                            onClick={() => setSelectedKey(k)}
                          >
                            Details
                          </button>
                          {!k.revoked_at && (
                            <button 
                              className="btn secondary small" 
                              style={{ color: "var(--error)", borderColor: "var(--error)" }} 
                              onClick={() => handleRevoke(k.key_id)}
                            >
                              Revoke
                            </button>
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

        <div className="card">
          <h2 style={{ marginBottom: "16px" }}>Generate New Key</h2>
          <form onSubmit={handleCreate} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <div>
              <label style={{ display: "block", marginBottom: "4px", fontSize: "0.85rem", fontWeight: 500 }}>Key Name / Label</label>
              <input 
                type="text" 
                required 
                value={name} 
                onChange={(e) => setName(e.target.value)} 
                className="input" 
                placeholder="e.g. Chatbot Assistant S2S" 
                style={{ width: "100%" }} 
              />
            </div>

            <div>
              <label style={{ display: "block", marginBottom: "4px", fontSize: "0.85rem", fontWeight: 500 }}>Environment</label>
              <select 
                className="input" 
                value={isSandbox ? "sandbox" : "live"} 
                onChange={(e) => setIsSandbox(e.target.value === "sandbox")}
                style={{ width: "100%" }}
              >
                <option value="live">Live (Production pk_live_)</option>
                <option value="sandbox">Sandbox (Testing pk_test_)</option>
              </select>
            </div>

            <div>
              <label style={{ display: "block", marginBottom: "4px", fontSize: "0.85rem", fontWeight: 500 }}>Rate Limit (req/min)</label>
              <input 
                type="number" 
                required 
                min={1} 
                max={10000} 
                value={rateLimit} 
                onChange={(e) => setRateLimit(Number(e.target.value))} 
                className="input" 
                style={{ width: "100%" }} 
              />
            </div>

            <div>
              <label style={{ display: "block", marginBottom: "4px", fontSize: "0.85rem", fontWeight: 500 }}>Daily Quota (req/day)</label>
              <input 
                type="number" 
                required 
                min={1} 
                max={1000000} 
                value={dailyQuota} 
                onChange={(e) => setDailyQuota(Number(e.target.value))} 
                className="input" 
                style={{ width: "100%" }} 
              />
            </div>

            <button type="submit" className="btn primary" style={{ marginTop: "4px" }}>
              Generate Key
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
