import React, { useEffect, useState } from "react";
import { 
  listWebhooks, 
  createWebhook, 
  updateWebhook, 
  deleteWebhook, 
  testWebhook,
  type Webhook, 
  type WebhookCreateResponse 
} from "../../api/client";
import { dateString } from "../../lib/format";

export default function PublisherWebhooks() {
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["impression"]);
  const [newWebhookData, setNewWebhookData] = useState<WebhookCreateResponse | null>(null);

  const loadData = async () => {
    try {
      setLoading(true);
      const data = await listWebhooks();
      setWebhooks(data);
      setError("");
    } catch (err: any) {
      setError(err.message || "Failed to load webhooks");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadData(); }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (events.length === 0) {
        alert("Please select at least one event type.");
        return;
    }
    try {
      const res = await createWebhook({ url, events, is_active: true });
      setNewWebhookData(res);
      setUrl("");
      setEvents(["impression"]);
      await loadData();
    } catch (err: any) {
      alert(err.message || "Failed to create webhook");
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Are you sure you want to delete this webhook?")) return;
    try {
      await deleteWebhook(id);
      await loadData();
    } catch (err: any) {
      alert("Failed to delete webhook");
    }
  };

  const handleToggle = async (id: number, currentStatus: boolean) => {
      try {
          await updateWebhook(id, { is_active: !currentStatus });
          await loadData();
      } catch (err: any) {
          alert("Failed to update webhook");
      }
  };
  
  const handleTest = async (id: number) => {
      try {
          alert("Testing webhook...");
          const res = await testWebhook(id);
          alert(`Test completed! Target returned status code: ${res.status_code || "Unknown"}`);
      } catch (err: any) {
          alert("Test failed: " + (err.message || String(err)));
      }
  };

  const copySecret = () => {
    if (newWebhookData) {
        navigator.clipboard.writeText(newWebhookData.secret);
        alert("Copied to clipboard!");
    }
  };

  const toggleEvent = (evt: string) => {
      if (events.includes(evt)) {
          setEvents(events.filter(e => e !== evt));
      } else {
          setEvents([...events, evt]);
      }
  };

  if (loading) return <div><p className="muted">Loading Webhooks...</p></div>;

  return (
    <div>
      <div className="flex row space-between align-center" style={{ marginBottom: "24px" }}>
        <div>
          <h1>Webhooks</h1>
          <p className="muted">Receive real-time HTTP POST notifications when events occur on your ad slots.</p>
        </div>
      </div>

      {error && <p className="error" style={{ marginBottom: "24px" }}>{error}</p>}

      {newWebhookData && (
        <div className="card" style={{ marginBottom: "24px", border: "2px solid #10b981" }}>
            <h2 style={{ color: "#10b981", marginBottom: "8px" }}>Webhook Created Successfully</h2>
            <p className="muted" style={{ marginBottom: "16px" }}>
                <strong>CRITICAL:</strong> Please copy your signing secret now. It is used to verify webhook payloads and will <strong>never be shown again</strong>.
            </p>
            <div className="flex row gap align-center">
                <input 
                    type="text" 
                    readOnly 
                    value={newWebhookData.secret} 
                    className="input" 
                    style={{ flex: 1, fontFamily: "monospace", backgroundColor: "var(--bg-alt)" }} 
                />
                <button className="btn primary" onClick={copySecret}>Copy Secret</button>
                <button className="btn secondary" onClick={() => setNewWebhookData(null)}>Done</button>
            </div>
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: "3fr 1fr", gap: "24px", alignItems: "start" }}>
        <div className="card">
          {webhooks.length === 0 ? (
            <p className="muted" style={{ padding: "40px", textAlign: "center" }}>No webhooks configured.</p>
          ) : (
            <table className="table">
                <thead>
                    <tr>
                        <th>URL</th>
                        <th>Events</th>
                        <th>Status</th>
                        <th>Created</th>
                        <th style={{ textAlign: "right" }}>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {webhooks.map(wh => (
                        <tr key={wh.id}>
                            <td style={{ wordBreak: "break-all", maxWidth: "250px" }}><b>{wh.url}</b></td>
                            <td>
                                <div className="flex gap" style={{ flexWrap: "wrap" }}>
                                    {wh.events.map(e => <span key={e} className="badge" style={{ fontSize: "11px" }}>{e}</span>)}
                                </div>
                            </td>
                            <td>
                                <button onClick={() => handleToggle(wh.id, wh.is_active)} className="btn small" style={{ background: "transparent", padding: 0, border: "none", cursor: "pointer" }}>
                                    {wh.is_active ? (
                                        <span className="badge" style={{ background: "var(--success)", color: "white" }}>Active</span>
                                    ) : (
                                        <span className="badge" style={{ background: "var(--muted)", color: "white" }}>Paused</span>
                                    )}
                                </button>
                            </td>
                            <td>{dateString(wh.created_at)}</td>
                            <td style={{ textAlign: "right" }}>
                                <div className="flex row gap justify-end">
                                    <button className="btn secondary small" onClick={() => handleTest(wh.id)}>Test</button>
                                    <button className="btn secondary small" style={{ color: "var(--error)", borderColor: "var(--error)" }} onClick={() => handleDelete(wh.id)}>
                                        Delete
                                    </button>
                                </div>
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
          )}
        </div>

        <div className="card">
            <h2 style={{ marginBottom: "16px" }}>Register Webhook</h2>
            <form onSubmit={handleCreate} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                <div>
                    <label style={{ display: "block", marginBottom: "4px" }}>Endpoint URL</label>
                    <input 
                        type="url" 
                        required 
                        value={url} 
                        onChange={(e) => setUrl(e.target.value)} 
                        className="input" 
                        placeholder="https://your-server.com/webhook" 
                        style={{ width: "100%" }} 
                    />
                </div>
                <div>
                    <label style={{ display: "block", marginBottom: "8px" }}>Subscribe to Events</label>
                    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                        {(["impression", "click", "conversion"]).map(evt => (
                            <label key={evt} style={{ display: "flex", alignItems: "center", gap: "8px", textTransform: "capitalize", cursor: "pointer" }}>
                                <input 
                                    type="checkbox" 
                                    checked={events.includes(evt)} 
                                    onChange={() => toggleEvent(evt)} 
                                />
                                {evt}
                            </label>
                        ))}
                    </div>
                </div>
                <button type="submit" className="btn primary" style={{ marginTop: "8px" }}>Register Webhook</button>
            </form>
        </div>
      </div>
    </div>
  );
}
