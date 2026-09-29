import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { 
  listPublisherProperties, 
  createPublisherProperty, 
  updatePublisherProperty, 
  PublisherProperty,
  listSlots,
  SlotData
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import Badge from "../../components/Badge";

export default function PublisherProperties() {
  const { session } = useAuth();
  const [properties, setProperties] = useState<PublisherProperty[]>([]);
  const [slots, setSlots] = useState<SlotData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");

  const publisherId = session?.user?.publisher_id;

  const loadData = async () => {
    if (!publisherId) return;
    try {
      setLoading(true);
      const [propsData, slotsData] = await Promise.all([
          listPublisherProperties(publisherId),
          listSlots(publisherId)
      ]);
      setProperties(propsData);
      setSlots(slotsData);
      setError("");
    } catch (err: any) {
      setError(err.message || "Failed to load properties");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadData(); }, [publisherId]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!publisherId) return;
    try {
      await createPublisherProperty(publisherId, { name, domain });
      setName("");
      setDomain("");
      await loadData();
    } catch (err: any) {
      alert(err.message || "Failed to create property");
    }
  };

  const handleToggleStatus = async (propId: number, currentStatus: number) => {
    try {
      await updatePublisherProperty(propId, { status: currentStatus === 1 ? 0 : 1 });
      await loadData();
    } catch (err: any) {
      alert("Failed to update status");
    }
  };

  if (loading) return <div><p className="muted">Loading properties...</p></div>;

  return (
    <div>
      <div className="flex row space-between align-center" style={{ marginBottom: "24px" }}>
        <div>
          <h1>My Properties</h1>
          <p className="muted">Manage your websites and apps where ads will be served.</p>
        </div>
      </div>

      {error && <p className="error" style={{ marginBottom: "24px" }}>{error}</p>}

      <div className="card" style={{ marginBottom: "24px" }}>
        {properties.length === 0 ? (
          <p className="muted" style={{ padding: "40px", textAlign: "center" }}>No properties found. Add a website or app to get started.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Domain</th>
                <th>Ad Slots</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {properties.map((prop) => {
                const propSlots = slots.filter(s => s.property_id === prop.id);
                return (
                  <tr key={prop.id}>
                    <td><b>{prop.name}</b></td>
                    <td>{prop.domain}</td>
                    <td>
                      {propSlots.length === 0 ? (
                          <span className="muted">No slots</span>
                      ) : (
                          <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                              {propSlots.map(s => (
                                  <span key={s.id} className="badge" style={{ fontSize: "11px", background: "var(--bg-alt)" }}>
                                      {s.name} ({s.width}x{s.height})
                                  </span>
                              ))}
                          </div>
                      )}
                    </td>
                    <td><Badge status={prop.status} /></td>
                    <td>
                      <button className="btn secondary small" onClick={() => handleToggleStatus(prop.id, prop.status)}>
                        {prop.status === 1 ? "Deactivate" : "Activate"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2 style={{ marginBottom: "16px" }}>Add New Property</h2>
        <form onSubmit={handleCreate} className="flex row gap align-center">
            <input
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="input"
              placeholder="Property Name (e.g. My Tech Blog)"
              style={{ flex: 1 }}
            />
            <input
              type="text"
              required
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              className="input"
              placeholder="Domain (e.g. example.com)"
              style={{ flex: 1 }}
            />
            <button type="submit" className="btn primary">Add Property</button>
        </form>
      </div>
    </div>
  );
}
