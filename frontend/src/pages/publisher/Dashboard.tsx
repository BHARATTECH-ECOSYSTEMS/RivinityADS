import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { createSlot, listSlots, listPublisherAssignments, getPublisherLedger, requestPayout, type SlotData, type PublisherAssignment, type LedgerResponse } from "../../api/client";
import Badge from "../../components/Badge";
import StatCard from "../../components/StatCard";
import { money, dateString } from "../../lib/format";
import { CREATIVE_TYPES } from "../../types";

const FORMAT_SIZES: Record<number, { width: number; height: number }> = {
  1: { width: 300, height: 250 },
  2: { width: 728, height: 90 },
  3: { width: 320, height: 50 },
  4: { width: 300, height: 600 },
};
export default function PublisherDashboard() {
  const { session } = useAuth();
  const [slots, setSlots] = useState<SlotData[]>([]);
  const [form, setForm] = useState({ slot_id: "", name: "", format: "1" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [assignments, setAssignments] = useState<PublisherAssignment[]>([]);
  
  const [ledger, setLedger] = useState<LedgerResponse | null>(null);
  const [payoutAmount, setPayoutAmount] = useState<number | "">("");

  const reload = () => {
    listSlots().then(setSlots).catch(() => setSlots([]));
    listPublisherAssignments().then(setAssignments).catch(() => setAssignments([]));
    if (session?.user.publisher_id) {
        getPublisherLedger(session.user.publisher_id).then(setLedger).catch(console.error);
    }
  };

  useEffect(() => { void reload(); }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const fmt = Number(form.format);
    const size = FORMAT_SIZES[fmt] ?? { width: 300, height: 250 };
    try {
      await createSlot({ slot_id: form.slot_id, name: form.name, format: fmt, ...size });
      setForm({ slot_id: "", name: "", format: "1" });
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create slot");
    } finally {
      setLoading(false);
    }
  };

  const avg = slots.length ? slots.reduce((s) => s + 0, 0) / slots.length : 0;

  return (
    <div>
      <h1>My Ad Slots</h1>

      <div className="grid cards">
        <StatCard title="Active slots" value={`${slots.filter((s) => s.status === 1).length} / ${slots.length}`} />
        <StatCard title="Avg eCPM today" value={money(avg)} />
        <StatCard title="Est. earnings today" value={money(0)} />
      </div>

      {/* Create Slot Form */}
      <div className="card">
        <h2>Register a slot</h2>
        {error && <p className="error">{error}</p>}
        <form onSubmit={submit}>
          <div className="form-grid">
            <div className="field">
              <label>Slot ID *</label>
              <input
                value={form.slot_id}
                onChange={(e) => setForm({ ...form, slot_id: e.target.value })}
                placeholder="e.g. banner_home_top"
                required
              />
            </div>
            <div className="field">
              <label>Friendly name *</label>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="e.g. Home Top Banner"
                required
              />
            </div>
            <div className="field">
              <label>Format</label>
              <select value={form.format} onChange={(e) => setForm({ ...form, format: e.target.value })}>
                {Object.entries(CREATIVE_TYPES).map(([v, l]) => (
                  <option key={v} value={v}>{l} ({FORMAT_SIZES[Number(v)]?.width}×{FORMAT_SIZES[Number(v)]?.height})</option>
                ))}
              </select>
            </div>
          </div>
          <button className="btn" disabled={loading}>{loading ? "Creating..." : "Create slot"}</button>
        </form>
      </div>

      {/* Slots Table */}
      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
          <h2 style={{ margin: 0 }}>Your Slots</h2>
          {slots.length > 0 && (
            <Link to="/publisher/preview" className="btn secondary small">
              🔴 Live Preview
            </Link>
          )}
        </div>
        {slots.length === 0 ? (
          <p className="muted">No slots yet. Create one above to get started.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Slot ID</th>
                <th>Name</th>
                <th>Format</th>
                <th>Size</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {slots.map((s) => (
                <tr key={s.id}>
                  <td><code>{s.slot_id}</code></td>
                  <td><b>{s.name}</b></td>
                  <td>{CREATIVE_TYPES[s.format]}</td>
                  <td>{s.width}×{s.height}</td>
                  <td><Badge status={s.status} /></td>
                  <td>
                    <Link
                      to={`/publisher/preview?slot_id=${encodeURIComponent(s.slot_id)}`}
                      className="btn secondary small"
                    >
                      Preview Ad
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Assigned Ads */}
      <div className="card" style={{ marginBottom: "24px" }}>
        <h2>Assigned Ads</h2>
        {assignments.length === 0 ? (
          <p className="muted">No campaigns are explicitly assigned to your slots.</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Campaign</th>
                  <th>Advertiser</th>
                  <th>Slot</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {assignments.map((a) => (
                  <tr key={a.id}>
                    <td><b>{a.campaign_name}</b></td>
                    <td>{a.advertiser_name}</td>
                    <td className="muted">{a.slot_id ? `${a.slot_name} (${a.slot_id})` : "All eligible slots"}</td>
                    <td>{a.status}</td>
                    <td>
                      <Link
                        to={`/publisher/preview${a.slot_id ? `?slot_id=${encodeURIComponent(a.slot_id)}` : ""}`}
                        className="btn secondary small"
                      >
                        Preview
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Earnings & Payouts */}
      <div className="card">
          <div className="flex row space-between align-center" style={{ marginBottom: "16px" }}>
              <h2 style={{ margin: 0 }}>Earnings & Payouts</h2>
              <div style={{ fontSize: "1.25rem", fontWeight: 700 }}>
                  Balance: {ledger ? money(ledger.balance) : "..."}
              </div>
          </div>
          <div className="flex row gap" style={{ alignItems: "center", marginBottom: "24px" }}>
              <input type="number" placeholder="Payout Amount ($)" value={payoutAmount} onChange={e => setPayoutAmount(Number(e.target.value) || "")} min={1} style={{ width: "200px" }} />
              <button 
                className="btn primary" 
                onClick={async () => {
                    if (!payoutAmount || payoutAmount <= 0) return;
                    if (!session?.user.publisher_id) return;
                    try {
                        await requestPayout(session.user.publisher_id, Number(payoutAmount), `demo_out_${Date.now()}`);
                        setPayoutAmount("");
                        reload();
                        alert("Payout requested successfully");
                    } catch (err) {
                        alert("Payout failed: " + (err instanceof Error ? err.message : String(err)));
                    }
                }} 
                disabled={!payoutAmount || !!(ledger && Number(payoutAmount) > ledger.balance)}
              >
                  Request Payout
              </button>
          </div>
          
          <h3 style={{ marginBottom: "12px", fontSize: "14px" }}>Recent Transactions</h3>
          {ledger?.transactions.length === 0 && <p className="muted" style={{ fontSize: "13px" }}>No transactions yet.</p>}
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {ledger?.transactions.slice(0, 10).map(tx => (
                  <div key={tx.id} className="flex row space-between align-center" style={{ fontSize: "13px", paddingBottom: "8px", borderBottom: "1px solid var(--border)" }}>
                      <div>
                          <div style={{ fontWeight: 600 }}>{tx.type === 3 ? "Payout Request" : "Earnings / Other"}</div>
                          <div className="muted">{dateString(tx.created_at)}</div>
                      </div>
                      <div style={{ fontWeight: 700, color: tx.amount > 0 ? "var(--success)" : "var(--fg)" }}>
                          {tx.amount > 0 ? "+" : ""}{money(tx.amount)}
                      </div>
                  </div>
              ))}
          </div>
      </div>
    </div>
  );
}
