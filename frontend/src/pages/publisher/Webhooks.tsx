import React, { useEffect, useState } from "react";
import { 
  listWebhooks, 
  createWebhook, 
  updateWebhook, 
  deleteWebhook, 
  testWebhook,
  listWebhookDeliveries,
  getWebhookDeliveryDetail,
  retryWebhookDelivery,
  type Webhook, 
  type WebhookCreateResponse,
  type WebhookDelivery,
  type WebhookDeliveryDetail
} from "../../api/client";
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

export default function PublisherWebhooks() {
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["impression"]);
  const [submitting, setSubmitting] = useState(false);
  const [newWebhookData, setNewWebhookData] = useState<WebhookCreateResponse | null>(null);
  const [copiedSecret, setCopiedSecret] = useState(false);

  // Delivery visibility state
  const [selectedWebhook, setSelectedWebhook] = useState<Webhook | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [loadingDeliveries, setLoadingDeliveries] = useState(false);
  const [retryingId, setRetryingId] = useState<number | null>(null);
  const [selectedDeliveryDetail, setSelectedDeliveryDetail] = useState<WebhookDeliveryDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Delete confirmation
  const [webhookToDelete, setWebhookToDelete] = useState<Webhook | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Test action status
  const [testingId, setTestingId] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<{ id: number; message: string; ok: boolean } | null>(null);

  const loadData = async () => {
    try {
      setLoading(true);
      const data = await listWebhooks();
      setWebhooks(data);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load webhooks");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { 
    void loadData(); 
  }, []);

  const loadDeliveries = async (wh: Webhook) => {
    try {
      setSelectedWebhook(wh);
      setLoadingDeliveries(true);
      const data = await listWebhookDeliveries(wh.id);
      setDeliveries(data);
    } catch (err: any) {
      setError("Failed to load deliveries: " + (err.message || String(err)));
    } finally {
      setLoadingDeliveries(false);
    }
  };

  const inspectDelivery = async (whId: number, deliveryId: number) => {
    setLoadingDetail(true);
    try {
      const detail = await getWebhookDeliveryDetail(whId, deliveryId);
      setSelectedDeliveryDetail(detail);
    } catch (err: any) {
      setError("Failed to load delivery payload: " + (err.message || String(err)));
    } finally {
      setLoadingDetail(false);
    }
  };

  const handleRetry = async (webhookId: number, deliveryId: number) => {
    try {
      setRetryingId(deliveryId);
      await retryWebhookDelivery(webhookId, deliveryId);
      if (selectedWebhook) {
        const updated = await listWebhookDeliveries(webhookId);
        setDeliveries(updated);
      }
    } catch (err: any) {
      setError("Retry failed: " + (err.message || String(err)));
    } finally {
      setRetryingId(null);
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (events.length === 0) {
      setError("Please select at least one event subscription.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await createWebhook({ url: url.trim(), events, is_active: true });
      setNewWebhookData(res);
      setUrl("");
      setEvents(["impression"]);
      await loadData();
    } catch (err: any) {
      setError(err.message || "Failed to create webhook");
    } finally {
      setSubmitting(false);
    }
  };

  const confirmDelete = async () => {
    if (!webhookToDelete) return;
    setDeleting(true);
    try {
      await deleteWebhook(webhookToDelete.id);
      if (selectedWebhook?.id === webhookToDelete.id) {
        setSelectedWebhook(null);
        setDeliveries([]);
      }
      setWebhookToDelete(null);
      await loadData();
    } catch (err: any) {
      setError("Failed to delete webhook");
    } finally {
      setDeleting(false);
    }
  };

  const handleToggle = async (id: number, currentStatus: boolean) => {
    try {
      await updateWebhook(id, { is_active: !currentStatus });
      await loadData();
    } catch (err: any) {
      setError("Failed to update webhook status");
    }
  };
  
  const handleTest = async (wh: Webhook) => {
    setTestingId(wh.id);
    setTestResult(null);
    try {
      const res = await testWebhook(wh.id);
      setTestResult({
        id: wh.id,
        message: `Test ping completed. Target status code: ${res.status_code || "OK"}`,
        ok: !!res.delivered,
      });
      await loadData();
      if (selectedWebhook?.id === wh.id) {
        await loadDeliveries(wh);
      }
    } catch (err: any) {
      setTestResult({
        id: wh.id,
        message: "Test ping failed: " + (err.message || String(err)),
        ok: false,
      });
    } finally {
      setTestingId(null);
    }
  };

  const copySecret = async () => {
    if (newWebhookData) {
      try {
        await navigator.clipboard.writeText(newWebhookData.secret);
        setCopiedSecret(true);
        setTimeout(() => setCopiedSecret(false), 2500);
      } catch {
        // Clipboard write failed
      }
    }
  };

  const toggleEvent = (evt: string) => {
    if (events.includes(evt)) {
      setEvents(events.filter((e) => e !== evt));
    } else {
      setEvents([...events, evt]);
    }
  };

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Developer Webhooks"
        title="Webhook Endpoints"
        description="Register HTTP POST destinations to receive signed real-time event dispatches for impressions, clicks, and conversions."
      />

      {error && (
        <Alert variant="danger" title="Webhook Notice" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* One-Time Webhook Secret Display */}
      {newWebhookData && (
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
              Webhook Endpoint Created Successfully
            </h3>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setNewWebhookData(null)}
              style={{ padding: "2px 6px" }}
            >
              ✕ Done
            </button>
          </div>
          <p style={{ fontSize: "0.875rem", color: "var(--color-text-secondary)", marginBottom: "16px" }}>
            <strong>CRITICAL SECURITY NOTICE:</strong> This HMAC signing secret is shown <strong>once</strong>. Store it securely in your receiving service to verify webhook signature headers (<code>X-Signature</code>).
          </p>
          <div style={{ display: "flex", gap: "10px", alignItems: "center", marginBottom: "12px" }}>
            <input 
              type="text" 
              readOnly 
              value={newWebhookData.secret} 
              className="input" 
              style={{ flex: 1, fontFamily: "var(--font-mono)", backgroundColor: "var(--color-bg-surface)", fontWeight: 600 }} 
            />
            <Button variant="primary" onClick={copySecret}>
              {copiedSecret ? "Copied Secret!" : "Copy Signing Secret"}
            </Button>
            <Button variant="secondary" onClick={() => setNewWebhookData(null)}>
              I Have Saved This Secret
            </Button>
          </div>
          <div style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
            Endpoint URL: <code>{newWebhookData.url}</code> | Events: <strong>{newWebhookData.events.join(", ")}</strong>
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "2.3fr 1.7fr", gap: "24px", alignItems: "start" }}>
        
        {/* Webhooks Endpoints Table */}
        <Card
          title="Active Webhook Endpoints"
          subtitle={`${webhooks.length} endpoints configured.`}
        >
          {loading ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px", padding: "16px 0" }}>
              <LoadingSkeleton variant="rect" height={50} />
              <LoadingSkeleton variant="rect" height={50} />
            </div>
          ) : webhooks.length === 0 ? (
            <EmptyState
              title="No webhook endpoints registered"
              description="Register an HTTPS URL below to start receiving real-time event dispatches."
            />
          ) : (
            <div className="table-container" style={{ overflowX: "auto" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Destination URL</th>
                    <th>Subscribed Events</th>
                    <th>Deliveries</th>
                    <th>Status</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {webhooks.map((wh) => (
                    <tr key={wh.id}>
                      <td>
                        <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.8125rem", wordBreak: "break-all" }}>
                          {wh.url}
                        </code>
                        <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "2px" }}>
                          Created: {dateString(wh.created_at)}
                        </div>
                        {testResult && testResult.id === wh.id && (
                          <div style={{ marginTop: "4px", fontSize: "0.75rem", color: testResult.ok ? "var(--color-success)" : "var(--color-danger)" }}>
                            {testResult.message}
                          </div>
                        )}
                      </td>
                      <td>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                          {wh.events.map((evt) => (
                            <span 
                              key={evt} 
                              style={{ 
                                fontSize: "10px", 
                                background: "var(--color-bg-subtle)", 
                                border: "1px solid var(--border-color)",
                                padding: "2px 6px", 
                                borderRadius: "var(--radius-sm)",
                                textTransform: "capitalize",
                                fontWeight: 600,
                              }}
                            >
                              {evt}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td>
                        <div style={{ fontSize: "0.8125rem" }}>
                          <span style={{ color: "var(--color-success)", fontWeight: 600 }}>{wh.deliveries_ok} ok</span>
                          {" · "}
                          <span style={{ color: wh.deliveries_failed > 0 ? "var(--color-danger)" : "var(--color-text-muted)", fontWeight: wh.deliveries_failed > 0 ? 600 : 400 }}>
                            {wh.deliveries_failed} failed
                          </span>
                        </div>
                      </td>
                      <td>
                        <StatusBadge 
                          status={wh.is_active ? "active" : "paused"} 
                          label={wh.is_active ? "Active" : "Paused"} 
                          size="sm" 
                        />
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <div style={{ display: "flex", gap: "6px", justifyContent: "flex-end" }}>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => void handleTest(wh)}
                            isLoading={testingId === wh.id}
                          >
                            Test
                          </Button>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => void loadDeliveries(wh)}
                          >
                            Logs
                          </Button>
                          <Button
                            variant="danger"
                            size="sm"
                            onClick={() => setWebhookToDelete(wh)}
                          >
                            Delete
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {/* Register Webhook Form */}
        <Card
          title="Register Webhook Endpoint"
          subtitle="Configure real-time event streaming destination."
        >
          <form onSubmit={handleCreate}>
            <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <div className="field">
                <label htmlFor="webhook-url" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                  Destination URL *
                </label>
                <input
                  id="webhook-url"
                  type="url"
                  required
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  className="input"
                  placeholder="https://api.yourdomain.com/webhooks/ads"
                  disabled={submitting}
                />
                <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "4px", display: "block" }}>
                  Must be a reachable HTTPS destination. Private/internal IPs are disallowed.
                </span>
              </div>

              <div>
                <label style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "6px", display: "block" }}>
                  Subscribed Event Topics *
                </label>
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  {[
                    { id: "impression", label: "Impression (ad rendered to visitor)" },
                    { id: "click", label: "Click (visitor clicked ad link)" },
                    { id: "conversion", label: "Conversion (action completed)" },
                  ].map((evt) => (
                    <label
                      key={evt.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "8px",
                        fontSize: "0.875rem",
                        cursor: "pointer",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={events.includes(evt.id)}
                        onChange={() => toggleEvent(evt.id)}
                        disabled={submitting}
                      />
                      {evt.label}
                    </label>
                  ))}
                </div>
              </div>

              <div style={{ marginTop: "8px" }}>
                <Button 
                  type="submit" 
                  variant="primary" 
                  isLoading={submitting} 
                  disabled={!url.trim() || events.length === 0}
                  style={{ width: "100%" }}
                >
                  Create Endpoint &amp; Generate Secret
                </Button>
              </div>
            </div>
          </form>
        </Card>
      </div>

      {/* Deliveries Logs Drawer / Card */}
      {selectedWebhook && (
        <Card
          title={`Delivery Logs: ${selectedWebhook.url}`}
          subtitle="Real-time attempt telemetry, HTTP status codes, and manual retry controls."
          style={{ marginTop: "24px" }}
          footer={
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <Button variant="secondary" size="sm" onClick={() => setSelectedWebhook(null)}>
                Close Logs
              </Button>
            </div>
          }
        >
          {loadingDeliveries ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px", padding: "16px 0" }}>
              <LoadingSkeleton variant="rect" height={40} />
              <LoadingSkeleton variant="rect" height={40} />
            </div>
          ) : deliveries.length === 0 ? (
            <EmptyState
              title="No delivery attempts logged"
              description="Deliveries appear here once subscribed events are triggered or test pings are sent."
            />
          ) : (
            <div className="table-container" style={{ overflowX: "auto" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Event Type</th>
                    <th>Status</th>
                    <th>Attempts</th>
                    <th>Last HTTP Status</th>
                    <th>Last Attempt</th>
                    <th>Error Detail</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {deliveries.map((d) => {
                    let statusVariant: "success" | "danger" | "pending" = "pending";
                    if (d.status === "delivered") statusVariant = "success";
                    else if (d.status === "failed") statusVariant = "danger";

                    return (
                      <tr key={d.id}>
                        <td>
                          <span style={{ fontWeight: 600, textTransform: "capitalize" }}>{d.event_type}</span>
                          {d.event_id && (
                            <div style={{ fontSize: "11px", color: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}>
                              {d.event_id}
                            </div>
                          )}
                        </td>
                        <td>
                          <StatusBadge 
                            status={statusVariant === "success" ? "active" : statusVariant === "danger" ? "failed" : "pending"} 
                            label={d.status} 
                            size="sm" 
                          />
                        </td>
                        <td>{d.attempts} / {d.max_attempts}</td>
                        <td>
                          {d.last_http_status ? (
                            <code style={{ fontWeight: 600 }}>{d.last_http_status}</code>
                          ) : (
                            <span style={{ color: "var(--color-text-muted)" }}>—</span>
                          )}
                        </td>
                        <td style={{ fontSize: "0.8125rem" }}>
                          {d.last_attempt_at ? dateString(d.last_attempt_at) : dateString(d.created_at)}
                        </td>
                        <td style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", maxWidth: "240px" }}>
                          {d.last_error || "—"}
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <div style={{ display: "flex", gap: "6px", justifyContent: "flex-end" }}>
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => void inspectDelivery(selectedWebhook.id, d.id)}
                            >
                              Payload
                            </Button>
                            {d.status === "failed" && (
                              <Button
                                variant="primary"
                                size="sm"
                                onClick={() => void handleRetry(selectedWebhook.id, d.id)}
                                isLoading={retryingId === d.id}
                              >
                                Retry
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
      )}

      {/* Payload Inspection Modal */}
      {selectedDeliveryDetail && (
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
              maxWidth: "600px",
              width: "100%",
              padding: "24px",
              boxShadow: "var(--shadow-xl)",
              border: "1px solid var(--border-color)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
              <h3 style={{ margin: 0, fontSize: "1.125rem", fontWeight: 700 }}>
                Delivery Payload (#{selectedDeliveryDetail.id})
              </h3>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setSelectedDeliveryDetail(null)}
                aria-label="Close dialog"
              >
                ✕
              </button>
            </div>
            <pre
              style={{
                backgroundColor: "var(--color-bg-inverse)",
                color: "#e2e8f0",
                padding: "16px",
                borderRadius: "var(--radius-md)",
                fontFamily: "var(--font-mono)",
                fontSize: "0.8125rem",
                overflowX: "auto",
                border: "1px solid #334155",
                maxHeight: "360px",
                lineHeight: 1.5,
              }}
            >
              {(() => {
                try {
                  return JSON.stringify(JSON.parse(selectedDeliveryDetail.payload), null, 2);
                } catch {
                  return selectedDeliveryDetail.payload;
                }
              })()}
            </pre>
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "16px" }}>
              <Button variant="secondary" onClick={() => setSelectedDeliveryDetail(null)}>
                Close
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        isOpen={!!webhookToDelete}
        title="Delete Webhook Endpoint"
        message={`Are you sure you want to delete webhook destination "${webhookToDelete?.url}"? Real-time event streaming to this endpoint will cease immediately.`}
        confirmText="Delete Webhook"
        variant="danger"
        isLoading={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setWebhookToDelete(null)}
      />
    </PageContainer>
  );
}
