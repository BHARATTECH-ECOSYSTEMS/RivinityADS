import React, { useEffect, useState } from "react";
import { 
  listPublisherApiKeys, 
  createPublisherApiKey, 
  revokePublisherApiKey, 
  type ApiKey, 
  type ApiKeyCreateResponse 
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { dateString } from "../../lib/format";

export default function PublisherApiKeys() {
  const { session } = useAuth();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  
  const [name, setName] = useState("");
  const [newKeyData, setNewKeyData] = useState<ApiKeyCreateResponse | null>(null);

  const publisherId = session?.user?.publisher_id;

  const loadData = async () => {
    if (!publisherId) return;
    try {
      setLoading(true);
      const data = await listPublisherApiKeys(publisherId);
      setKeys(data);
      setError("");
    } catch (err: any) {
      setError(err.message || "Failed to load API keys");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadData(); }, [publisherId]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!publisherId) return;
    try {
      const res = await createPublisherApiKey(publisherId, name || "Default Key");
      setNewKeyData(res);
      setName("");
      await loadData();
    } catch (err: any) {
      alert(err.message || "Failed to create API key");
    }
  };

  const handleRevoke = async (keyId: number) => {
    if (!publisherId) return;
    if (!confirm("Are you sure you want to revoke this key? Any integrations using it will immediately fail.")) return;
    try {
      await revokePublisherApiKey(publisherId, keyId);
      await loadData();
    } catch (err: any) {
      alert("Failed to revoke API key");
    }
  };

  const copyKey = () => {
    if (newKeyData) {
        navigator.clipboard.writeText(newKeyData.raw_key);
        alert("Copied to clipboard!");
    }
  };

  if (loading) return <div><p className="muted">Loading API Keys...</p></div>;

  return (
    <div>
      <div className="flex row space-between align-center" style={{ marginBottom: "24px" }}>
        <div>
          <h1>API Keys</h1>
          <p className="muted">Manage keys to authenticate ad requests from your servers or third-party platforms.</p>
        </div>
      </div>

      {error && <p className="error" style={{ marginBottom: "24px" }}>{error}</p>}

      {newKeyData && (
        <div className="card" style={{ marginBottom: "24px", border: "2px solid #10b981" }}>
            <h2 style={{ color: "#10b981", marginBottom: "8px" }}>API Key Created Successfully</h2>
            <p className="muted" style={{ marginBottom: "16px" }}>
                <strong>CRITICAL:</strong> Please copy this key now. For your security, it will <strong>never be shown again</strong> once you close this page.
            </p>
            <div className="flex row gap align-center">
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
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: "3fr 1fr", gap: "24px", alignItems: "start" }}>
        <div className="card">
          {keys.length === 0 ? (
            <p className="muted" style={{ padding: "40px", textAlign: "center" }}>No API keys found. Generate one to authenticate your ad requests.</p>
          ) : (
            <table className="table">
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Prefix</th>
                        <th>Created</th>
                        <th>Status</th>
                        <th style={{ textAlign: "right" }}>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {keys.map(k => (
                        <tr key={k.id}>
                            <td><b>{k.name}</b></td>
                            <td style={{ fontFamily: "monospace", color: "var(--muted)" }}>{k.key_prefix}...</td>
                            <td>{dateString(k.created_at)}</td>
                            <td>
                                {k.revoked_at ? (
                                    <span className="badge" style={{ background: "var(--error)", color: "white" }}>Revoked</span>
                                ) : (
                                    <span className="badge" style={{ background: "var(--success)", color: "white" }}>Active</span>
                                )}
                            </td>
                            <td style={{ textAlign: "right" }}>
                                {!k.revoked_at && (
                                    <button className="btn secondary small" style={{ color: "var(--error)", borderColor: "var(--error)" }} onClick={() => handleRevoke(k.id)}>
                                        Revoke
                                    </button>
                                )}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
          )}
        </div>

        <div className="card">
            <h2 style={{ marginBottom: "16px" }}>Generate New Key</h2>
            <form onSubmit={handleCreate} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                <div>
                    <label style={{ display: "block", marginBottom: "4px" }}>Key Name / Label</label>
                    <input 
                        type="text" 
                        required 
                        value={name} 
                        onChange={(e) => setName(e.target.value)} 
                        className="input" 
                        placeholder="e.g. Production Mobile App" 
                        style={{ width: "100%" }} 
                    />
                </div>
                <button type="submit" className="btn primary" style={{ marginTop: "8px" }}>Generate Key</button>
            </form>
        </div>
      </div>
    </div>
  );
}
