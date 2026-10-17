import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  listSlots,
  createSlot,
  updateSlot,
  type SlotData,
  listPublisherProperties,
  type PublisherProperty,
  generateSlotTag
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import Badge from "../../components/Badge";

export default function PublisherAdUnits() {
  const { session } = useAuth();
  const [slots, setSlots] = useState<SlotData[]>([]);
  const [properties, setProperties] = useState<PublisherProperty[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  
  const [showTagModal, setShowTagModal] = useState(false);
  const [currentTag, setCurrentTag] = useState("");
  
  const [name, setName] = useState("");
  const [slotId, setSlotId] = useState("");
  const [format, setFormat] = useState(1);
  const [width, setWidth] = useState(300);
  const [height, setHeight] = useState(250);
  const [floorPrice, setFloorPrice] = useState(0.0);
  const [propertyId, setPropertyId] = useState<number | "">("");
  const [isChatSurface, setIsChatSurface] = useState(false);
  const [creating, setCreating] = useState(false);
  const [formSuccess, setFormSuccess] = useState("");
  const [formError, setFormError] = useState("");

  const publisherId = session?.user?.publisher_id;

  const loadData = async () => {
    if (!publisherId) return;
    try {
      setLoading(true);
      const [slotsData, propsData] = await Promise.all([
        listSlots(publisherId),
        listPublisherProperties(publisherId)
      ]);
      setSlots(slotsData);
      setProperties(propsData);
      setError("");
    } catch (err: any) {
      setError(err.message || "Failed to load ad units");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadData(); }, [publisherId]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setFormError("");
    setFormSuccess("");
    try {
      await createSlot({
        slot_id: slotId,
        name,
        format,
        width,
        height,
        floor_price: floorPrice,
        property_id: propertyId === "" ? null : propertyId,
        is_chat_surface: isChatSurface,
      });
      setName("");
      setSlotId("");
      setFloorPrice(0);
      setPropertyId("");
      setIsChatSurface(false);
      setFormSuccess("Ad unit created successfully!");
      await loadData();
    } catch (err: any) {
      setFormError(err.message || "Failed to create ad unit");
    } finally {
      setCreating(false);
    }
  };

  const handleToggleStatus = async (id: number, currentStatus: number) => {
      try {
          await updateSlot(id, { status: currentStatus === 1 ? 0 : 1 } as any);
          await loadData();
      } catch (err) {
          alert("Failed to update status");
      }
  };

  const handleToggleChatSurface = async (id: number, current: boolean) => {
      try {
          await updateSlot(id, { is_chat_surface: !current } as any);
          await loadData();
      } catch (err) {
          alert("Failed to update chat surface setting");
      }
  };

  const handleGetTag = async (id: number) => {
    try {
      const res = await generateSlotTag(id);
      setCurrentTag(res.html);
      setShowTagModal(true);
    } catch (err: any) {
      alert("Failed to generate tag");
    }
  };

  const copyTag = () => {
    navigator.clipboard.writeText(currentTag);
    alert("Copied to clipboard!");
  };

  if (loading) return <div><p className="muted">Loading ad units...</p></div>;

  return (
    <div>
      <div className="flex row space-between align-center" style={{ marginBottom: "24px" }}>
        <div>
          <h1>Ad Units</h1>
          <p className="muted">Manage the ad placements on your properties.</p>
        </div>
      </div>

      {error && <p className="error" style={{ marginBottom: "24px" }}>{error}</p>}

      <div className="grid" style={{ gridTemplateColumns: "3fr 1fr", gap: "24px", alignItems: "start" }}>
        <div className="card">
          {slots.length === 0 ? (
            <p className="muted" style={{ padding: "40px", textAlign: "center" }}>No ad units found. Create one to get your embed tags.</p>
          ) : (
            <table className="table">
                <thead>
                    <tr>
                        <th>Name & ID</th>
                        <th>Property</th>
                        <th>Surface</th>
                        <th>Size</th>
                        <th>Floor Price</th>
                        <th>Status</th>
                        <th style={{ textAlign: "right" }}>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {slots.map(slot => {
                        const prop = properties.find(p => p.id === slot.property_id);
                        return (
                            <tr key={slot.id}>
                                <td>
                                    <div><b>{slot.name}</b></div>
                                    <div className="muted" style={{ fontSize: "12px", fontFamily: "monospace" }}>{slot.slot_id}</div>
                                </td>
                                <td>{prop ? prop.name : <span className="muted">None</span>}</td>
                                <td>
                                    {slot.is_chat_surface ? (
                                        <span style={{ background: "#7c3aed", color: "#fff", fontSize: "11px", padding: "2px 8px", borderRadius: "999px", fontWeight: 600 }}>
                                            💬 Chat Surface
                                        </span>
                                    ) : (
                                        <span className="muted" style={{ fontSize: "12px" }}>Standard</span>
                                    )}
                                </td>
                                <td>{slot.width}x{slot.height}</td>
                                <td>${Number(slot.floor_price || 0).toFixed(2)}</td>
                                <td><Badge status={slot.status} /></td>
                                <td style={{ textAlign: "right" }}>
                                    <div className="flex row gap justify-end">
                                        <button className="btn secondary small" onClick={() => handleGetTag(slot.id)}>Code</button>
                                        <button className="btn secondary small" onClick={() => handleToggleChatSurface(slot.id, slot.is_chat_surface)} title="Toggle Chat Surface restriction">
                                            {slot.is_chat_surface ? "Standard" : "Chat"}
                                        </button>
                                        <button className="btn secondary small" onClick={() => handleToggleStatus(slot.id, slot.status)}>Toggle</button>
                                    </div>
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
          )}
        </div>

        <div className="card">
            <h2 style={{ marginBottom: "16px" }}>Create Ad Unit</h2>
            {formError && <p className="error" style={{ fontSize: "12px", marginBottom: "8px" }}>{formError}</p>}
            {formSuccess && <p style={{ color: "#16a34a", fontSize: "12px", marginBottom: "8px" }}>{formSuccess}</p>}
            <form onSubmit={handleCreate} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                <div>
                    <label style={{ display: "block", marginBottom: "4px" }}>Name</label>
                    <input type="text" required value={name} onChange={(e) => setName(e.target.value)} className="input" placeholder="Sidebar Top" style={{ width: "100%" }} />
                </div>
                <div>
                    <label style={{ display: "block", marginBottom: "4px" }}>Slot ID (Unique string)</label>
                    <input type="text" required value={slotId} onChange={(e) => setSlotId(e.target.value)} className="input" placeholder="sidebar_300_250" style={{ width: "100%", fontFamily: "monospace" }} />
                </div>
                <div>
                    <label style={{ display: "block", marginBottom: "4px" }}>Property (Website)</label>
                    <select value={propertyId} onChange={(e) => setPropertyId(e.target.value === "" ? "" : Number(e.target.value))} className="input" style={{ width: "100%" }}>
                        <option value="">None (Standalone)</option>
                        {properties.map(p => (
                            <option key={p.id} value={p.id}>{p.name}</option>
                        ))}
                    </select>
                </div>
                <div className="flex row gap">
                    <div style={{ flex: 1 }}>
                        <label style={{ display: "block", marginBottom: "4px" }}>Width (px)</label>
                        <input type="number" required value={width} onChange={(e) => setWidth(Number(e.target.value))} className="input" style={{ width: "100%" }} />
                    </div>
                    <div style={{ flex: 1 }}>
                        <label style={{ display: "block", marginBottom: "4px" }}>Height (px)</label>
                        <input type="number" required value={height} onChange={(e) => setHeight(Number(e.target.value))} className="input" style={{ width: "100%" }} />
                    </div>
                </div>
                <div>
                    <label style={{ display: "block", marginBottom: "4px" }}>Floor Price ($)</label>
                    <input type="number" step="0.01" required value={floorPrice} onChange={(e) => setFloorPrice(Number(e.target.value))} className="input" style={{ width: "100%" }} />
                </div>
                <div style={{ padding: "6px 0", borderTop: "1px solid var(--border, #e2e8f0)", borderBottom: "1px solid var(--border, #e2e8f0)" }}>
                    <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer", fontWeight: 500, fontSize: "13px" }}>
                        <input
                            type="checkbox"
                            checked={isChatSurface}
                            onChange={(e) => setIsChatSurface(e.target.checked)}
                        />
                        <span>💬 Chat Surface Placement</span>
                    </label>
                    <p className="muted" style={{ fontSize: "11px", margin: "4px 0 0 24px", lineHeight: "1.4" }}>
                        For conversational AI surfaces. <b>Interstitial ads cannot be served on chat surfaces.</b>
                    </p>
                </div>
                <button type="submit" className="btn primary" disabled={creating} style={{ marginTop: "8px" }}>
                    {creating ? "Creating..." : "Create Ad Unit"}
                </button>
            </form>
        </div>
      </div>

      {showTagModal && (
        <div style={{ position: "fixed", inset: 0, backgroundColor: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50, padding: "16px" }}>
          <div className="card" style={{ maxWidth: "600px", width: "100%" }}>
            <div className="flex row space-between align-center" style={{ marginBottom: "16px" }}>
              <h2>Integration Code</h2>
              <button onClick={() => setShowTagModal(false)} className="btn secondary small">✕</button>
            </div>
            <p className="muted" style={{ marginBottom: "16px" }}>Copy and paste this HTML snippet into your website exactly where you want the ad to appear.</p>
            <div style={{ backgroundColor: "var(--bg-alt)", padding: "16px", borderRadius: "8px", position: "relative", marginBottom: "16px" }}>
              <pre style={{ fontSize: "12px", overflowX: "auto", whiteSpace: "pre-wrap", fontFamily: "monospace", margin: 0 }}>
                {currentTag}
              </pre>
            </div>
            <div className="flex row gap justify-end">
              <button onClick={() => setShowTagModal(false)} className="btn secondary">Close</button>
              <button onClick={copyTag} className="btn primary">Copy Code</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
