import React, { useEffect, useState, type FormEvent } from "react";
import { 
  createAdvertiser, 
  listAdvertisers, 
  setAdvertiserStatus 
} from "../../api/client"; 
import { money } from "../../lib/format"; 
import type { Advertiser } from "../../types";
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

export default function Advertisers() {
  const [rows, setRows] = useState<Advertiser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formFeedback, setFormFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // Form state
  const [form, setForm] = useState({
    name: "",
    company: "",
    contact_email: "",
    balance: "1000",
    daily_budget: "100",
  });
  const [submitting, setSubmitting] = useState(false);

  // Search filter
  const [search, setSearch] = useState("");

  // Status toggle confirmation
  const [pendingToggle, setPendingToggle] = useState<{ id: number; currentStatus: number; name: string } | null>(null);
  const [toggleLoading, setToggleLoading] = useState(false);

  const reload = async () => {
    try {
      setLoading(true);
      const data = await listAdvertisers();
      setRows(data);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load advertisers");
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
      await createAdvertiser({
        name: form.name.trim(),
        company: form.company.trim() || undefined,
        contact_email: form.contact_email.trim() || undefined,
        balance: Number(form.balance) || 0,
        daily_budget: Number(form.daily_budget) || 0,
      });
      setFormFeedback({ type: "success", message: `Advertiser "${form.name}" created successfully.` });
      setForm({ name: "", company: "", contact_email: "", balance: "1000", daily_budget: "100" });
      await reload();
    } catch (err: any) {
      setFormFeedback({ type: "error", message: err.message || "Failed to create advertiser" });
    } finally {
      setSubmitting(false);
    }
  };

  const confirmToggleStatus = async () => {
    if (!pendingToggle) return;
    setToggleLoading(true);
    try {
      const nextStatus = pendingToggle.currentStatus === 1 ? 0 : 1;
      await setAdvertiserStatus(pendingToggle.id, nextStatus);
      setPendingToggle(null);
      await reload();
    } catch (err: any) {
      setError(err.message || "Failed to update advertiser status");
    } finally {
      setToggleLoading(false);
    }
  };

  const filteredRows = rows.filter((a) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return (
      a.name.toLowerCase().includes(q) ||
      String(a.id).includes(q) ||
      (a.company && a.company.toLowerCase().includes(q)) ||
      (a.contact_email && a.contact_email.toLowerCase().includes(q))
    );
  });

  return (
    <PageContainer>
      <PageHeader
        eyebrow="Account Directory"
        title="Advertisers"
        description="Manage advertising accounts, monitor wallet balances and daily budgets, and control platform access."
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
        
        {/* Advertisers Directory Card */}
        <Card
          title="Registered Advertisers"
          subtitle={`${rows.length} advertiser accounts in system.`}
        >
          <div style={{ marginBottom: "16px" }}>
            <input
              type="text"
              placeholder="Filter by name, company, or email..."
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
              title="No advertisers found"
              description={search ? "No advertisers match your search query." : "Register an advertiser using the form."}
            />
          ) : (
            <div className="table-container" style={{ overflowX: "auto" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Advertiser</th>
                    <th>Email</th>
                    <th>Balance</th>
                    <th>Daily Budget</th>
                    <th>Status</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((a) => {
                    const isActive = a.status === 1;

                    return (
                      <tr key={a.id}>
                        <td>
                          <div style={{ fontWeight: 600 }}>{a.name}</div>
                          <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>
                            ID #{a.id} {a.company ? `· ${a.company}` : ""}
                          </div>
                        </td>
                        <td style={{ fontSize: "0.8125rem" }}>
                          {a.contact_email || <span style={{ color: "var(--color-text-muted)" }}>—</span>}
                        </td>
                        <td style={{ fontWeight: 600, fontSize: "0.875rem" }}>
                          {money(a.balance)}
                        </td>
                        <td style={{ fontSize: "0.8125rem" }}>
                          {money(a.daily_budget)}
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
                            onClick={() => setPendingToggle({ id: a.id, currentStatus: a.status, name: a.name })}
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

        {/* Add Advertiser Form Card */}
        <Card
          title="Add New Advertiser"
          subtitle="Provision a new advertiser organization and initial balance."
        >
          <form onSubmit={submit}>
            <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <div className="field">
                <label htmlFor="adv-name" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                  Account / Brand Name *
                </label>
                <input
                  id="adv-name"
                  type="text"
                  required
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="input"
                  placeholder="e.g. Acme Corporation"
                  disabled={submitting}
                />
              </div>

              <div className="field">
                <label htmlFor="adv-company" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                  Company Legal Entity
                </label>
                <input
                  id="adv-company"
                  type="text"
                  value={form.company}
                  onChange={(e) => setForm({ ...form, company: e.target.value })}
                  className="input"
                  placeholder="e.g. Acme Global Inc."
                  disabled={submitting}
                />
              </div>

              <div className="field">
                <label htmlFor="adv-email" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                  Contact Email
                </label>
                <input
                  id="adv-email"
                  type="email"
                  value={form.contact_email}
                  onChange={(e) => setForm({ ...form, contact_email: e.target.value })}
                  className="input"
                  placeholder="billing@acme.com"
                  disabled={submitting}
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div className="field">
                  <label htmlFor="adv-balance" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                    Opening Balance ($)
                  </label>
                  <input
                    id="adv-balance"
                    type="number"
                    min="0"
                    step="any"
                    value={form.balance}
                    onChange={(e) => setForm({ ...form, balance: e.target.value })}
                    className="input"
                    disabled={submitting}
                  />
                </div>
                <div className="field">
                  <label htmlFor="adv-budget" style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "4px", display: "block" }}>
                    Daily Budget Limit ($)
                  </label>
                  <input
                    id="adv-budget"
                    type="number"
                    min="0"
                    step="any"
                    value={form.daily_budget}
                    onChange={(e) => setForm({ ...form, daily_budget: e.target.value })}
                    className="input"
                    disabled={submitting}
                  />
                </div>
              </div>

              <div style={{ marginTop: "12px" }}>
                <Button
                  type="submit"
                  variant="primary"
                  isLoading={submitting}
                  disabled={!form.name.trim()}
                  style={{ width: "100%" }}
                >
                  Create Advertiser
                </Button>
              </div>
            </div>
          </form>
        </Card>
      </div>

      {/* Confirmation Dialog for Status Change */}
      <ConfirmDialog
        isOpen={!!pendingToggle}
        title={pendingToggle?.currentStatus === 1 ? "Pause Advertiser" : "Activate Advertiser"}
        message={
          pendingToggle?.currentStatus === 1
            ? `Are you sure you want to pause "${pendingToggle?.name}"? All active campaigns and ad sets belonging to this advertiser will stop serving.`
            : `Are you sure you want to activate "${pendingToggle?.name}"? Associated active campaigns will resume serving according to budget pacing.`
        }
        confirmText={pendingToggle?.currentStatus === 1 ? "Pause Advertiser" : "Activate Advertiser"}
        variant={pendingToggle?.currentStatus === 1 ? "danger" : "primary"}
        isLoading={toggleLoading}
        onConfirm={confirmToggleStatus}
        onCancel={() => setPendingToggle(null)}
      />
    </PageContainer>
  );
}
