import React, { useEffect, useState, type FormEvent } from "react";
import { 
  createPublisher, 
  listPublishers, 
  listSlots, 
  setPublisherStatus,
  type SlotData 
} from "../../api/client";
import type { Publisher } from "../../types";
import {
  PageContainer,
  PageHeader,
  Card,
  Button,
  StatusBadge,
  Alert,
  LoadingSkeleton,
  EmptyState,
  ConfirmDialog,
} from "../../components/ui";

export default function Publishers() {
  const [rows, setRows] = useState<Publisher[]>([]);
  const [slots, setSlots] = useState<SlotData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formFeedback, setFormFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // Form state
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [siteUrl, setSiteUrl] = useState("");
  const [isSandbox, setIsSandbox] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Search filter
  const [search, setSearch] = useState("");

  // Status toggle confirmation
  const [pendingToggle, setPendingToggle] = useState<{ id: number; currentStatus: number; name: string } | null>(null);
  const [toggleLoading, setToggleLoading] = useState(false);

  const reload = async () => {
    try {
      setLoading(true);
      const [p, s] = await Promise.all([listPublishers(), listSlots()]);
      setRows(p);
      setSlots(s);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load publishers");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setFormFeedback(null);
    try {
      await createPublisher({
        name: name.trim(),
        email: email.trim() || undefined,
        site_url: siteUrl.trim() || undefined,
        is_sandbox: isSandbox,
      });
      setFormFeedback({ type: "success", message: `Publisher "${name}" registered successfully.` });
      setName("");
      setEmail("");
      setSiteUrl("");
      setIsSandbox(false);
      await reload();
    } catch (err: any) {
      setFormFeedback({ type: "error", message: err.message || "Failed to create publisher" });
    } finally {
      setSubmitting(false);
    }
  };

  const confirmToggleStatus = async () => {
    if (!pendingToggle) return;
    setToggleLoading(true);
    try {
      const nextStatus = pendingToggle.currentStatus === 1 ? 0 : 1;
      await setPublisherStatus(pendingToggle.id, nextStatus);
      setPendingToggle(null);
      await reload();
    } catch (err: any) {
      setError(err.message || "Failed to update publisher status");
    } finally {
      setToggleLoading(false);
    }
  };

  const filteredRows = rows.filter((p) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return (
      p.name.toLowerCase().includes(q) ||
      String(p.id).includes(q) ||
      (p.email && p.email.toLowerCase().includes(q)) ||
      (p.site_url && p.site_url.toLowerCase().includes(q))
    );
  });

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Publisher Directory"
        title="Publishers"
        description="Manage registered publisher media organizations, monitor ad inventory slots, and configure operational status."
        actions={
          <Button variant="secondary" size="sm" onClick={reload} isLoading={loading}>
            Refresh
          </Button>
        }
      />

      {error && (
        <Alert variant="danger" title="Error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {formFeedback && (
        <Alert
          variant={formFeedback.type === "success" ? "success" : "danger"}
          onClose={() => setFormFeedback(null)}
        >
          {formFeedback.message}
        </Alert>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "2.4fr 1.6fr", gap: "24px", alignItems: "start" }}>
        
        {/* Publishers Directory Card */}
        <Card
          title="Registered Publishers"
          subtitle={`${rows.length} publisher accounts (${slots.length} total ad placement slots registered).`}
        >
          <div style={{ marginBottom: "16px" }}>
            <input
              type="text"
              placeholder="Filter by name, email, or site..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="input"
              style={{ fontSize: "0.8125rem", padding: "6px 10px" }}
            />
          </div>

          {loading ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px", padding: "16px 0" }}>
              <LoadingSkeleton variant="rect" height={40} />
              <LoadingSkeleton variant="rect" height={40} />
            </div>
          ) : filteredRows.length === 0 ? (
            <EmptyState
              title="No publishers found"
              description={search ? "No publishers match your search query." : "Register a publisher using the form."}
            />
          ) : (
            <div className="table-container" style={{ overflowX: "auto" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Publisher Organization</th>
                    <th>Email</th>
                    <th>Website / Media Domain</th>
                    <th>Slots</th>
                    <th>Status</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((p) => {
                    const pubSlots = slots.filter((s) => s.publisher_id === p.id);
                    const isActive = p.status === 1;

                    return (
                      <tr key={p.id}>
                        <td>
                          <div style={{ fontWeight: 600 }}>{p.name}</div>
                          <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>
                            ID #{p.id}
                          </div>
                        </td>
                        <td style={{ fontSize: "0.8125rem" }}>
                          {p.email || <span style={{ color: "var(--color-text-muted)" }}>—</span>}
                        </td>
                        <td style={{ fontSize: "0.8125rem" }}>
                          {p.site_url ? (
                            <a href={p.site_url} target="_blank" rel="noreferrer" style={{ color: "var(--color-primary)" }}>
                              {p.site_url}
                            </a>
                          ) : (
                            <span style={{ color: "var(--color-text-muted)" }}>—</span>
                          )}
                        </td>
                        <td style={{ fontSize: "0.8125rem", fontWeight: 600 }}>
                          {pubSlots.length} slots
                        </td>
                        <td>
                          <StatusBadge 
                            status={isActive ? "active" : "paused"} 
                            label={isActive ? "Active" : "Paused"} 
                            size="sm" 
                          />
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <Button
                            variant={isActive ? "secondary" : "primary"}
                            size="sm"
                            onClick={() => setPendingToggle({ id: p.id, currentStatus: p.status, name: p.name })}
                          >
                            {isActive ? "Pause" : "Activate"}
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {/* Add Publisher Form Card */}
        <Card
          title="Onboard New Publisher"
          subtitle="Provision a new publisher account and media profile."
        >
          <form onSubmit={submit}>
            <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <div className="field">
                <label htmlFor="pub-name" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                  Publisher / Media Name *
                </label>
                <input
                  id="pub-name"
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="input"
                  placeholder="e.g. Daily Tech Wire"
                  disabled={submitting}
                />
              </div>

              <div className="field">
                <label htmlFor="pub-email" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                  Contact Email
                </label>
                <input
                  id="pub-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="input"
                  placeholder="publisher@dailytech.com"
                  disabled={submitting}
                />
              </div>

              <div className="field">
                <label htmlFor="pub-url" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                  Site or App URL
                </label>
                <input
                  id="pub-url"
                  type="url"
                  value={siteUrl}
                  onChange={(e) => setSiteUrl(e.target.value)}
                  className="input"
                  placeholder="https://dailytech.com"
                  disabled={submitting}
                />
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "4px" }}>
                <input
                  id="pub-sandbox"
                  type="checkbox"
                  checked={isSandbox}
                  onChange={(e) => setIsSandbox(e.target.checked)}
                  disabled={submitting}
                />
                <label htmlFor="pub-sandbox" style={{ fontSize: "0.875rem", cursor: "pointer" }}>
                  Sandbox Account (testing environment)
                </label>
              </div>

              <div style={{ marginTop: "12px" }}>
                <Button
                  type="submit"
                  variant="primary"
                  isLoading={submitting}
                  disabled={!name.trim()}
                  style={{ width: "100%" }}
                >
                  Create Publisher
                </Button>
              </div>
            </div>
          </form>
        </Card>
      </div>

      {/* Confirmation Dialog for Status Change */}
      <ConfirmDialog
        isOpen={!!pendingToggle}
        title={pendingToggle?.currentStatus === 1 ? "Pause Publisher" : "Activate Publisher"}
        message={
          pendingToggle?.currentStatus === 1
            ? `Are you sure you want to pause "${pendingToggle?.name}"? Ad serving on all associated properties and units will be suspended.`
            : `Are you sure you want to activate "${pendingToggle?.name}"? Associated active ad units will resume serving.`
        }
        confirmText={pendingToggle?.currentStatus === 1 ? "Pause Publisher" : "Activate Publisher"}
        variant={pendingToggle?.currentStatus === 1 ? "danger" : "primary"}
        isLoading={toggleLoading}
        onConfirm={confirmToggleStatus}
        onCancel={() => setPendingToggle(null)}
      />
    </PageContainer>
  );
}
