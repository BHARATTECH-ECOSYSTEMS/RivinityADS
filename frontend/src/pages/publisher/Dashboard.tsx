import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { createSlot, listSlots, type SlotData } from "../../api/client";
import Badge from "../../components/Badge";
import StatCard from "../../components/StatCard";
import { money } from "../../lib/format";
import { CREATIVE_TYPES } from "../../types";

const FORMAT_SIZES: Record<number, { width: number; height: number }> = {
  1: { width: 300, height: 250 },
  2: { width: 728, height: 90 },
  3: { width: 320, height: 50 },
  4: { width: 300, height: 600 },
};

export default function PublisherDashboard() {
  const [slots, setSlots] = useState<SlotData[]>([]);
  const [form, setForm] = useState({ slot_id: "", name: "", format: "1" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = () => listSlots().then(setSlots).catch(() => setSlots([]));

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
    </div>
  );
}
