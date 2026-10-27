import React, { useEffect, useState } from "react";
import {
  listCampaignAssignments,
  createCampaignAssignment,
  deleteCampaignAssignment,
  listPublishers,
  listSlots,
  type CampaignAssignment,
  type SlotData
} from "../../api/client";
import { type Campaign, type Publisher } from "../../types";
import {
  Button,
  StatusBadge,
  Alert,
  LoadingSkeleton,
  EmptyState,
  ConfirmDialog
} from "../../components/ui";

interface Props {
  campaign: Campaign | null;
  onClose: () => void;
}

export default function CampaignAssignmentModal({ campaign, onClose }: Props) {
  const [assignments, setAssignments] = useState<CampaignAssignment[]>([]);
  const [publishers, setPublishers] = useState<Publisher[]>([]);
  const [slots, setSlots] = useState<SlotData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedPublisher, setSelectedPublisher] = useState<number | "">("");
  const [selectedSlot, setSelectedSlot] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);

  // Deletion confirmation state
  const [assignmentToDelete, setAssignmentToDelete] = useState<CampaignAssignment | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (!campaign) return;
    setLoading(true);
    setError(null);
    Promise.all([
      listCampaignAssignments(campaign.id),
      listPublishers(),
      listSlots()
    ])
      .then(([assgn, pubs, slts]) => {
        setAssignments(assgn);
        setPublishers(pubs);
        setSlots(slts);
      })
      .catch((err) => {
        setError(err.message || "Failed to load campaign assignments");
      })
      .finally(() => setLoading(false));
  }, [campaign]);

  if (!campaign) return null;

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedPublisher) return;
    setSubmitting(true);
    setError(null);
    try {
      await createCampaignAssignment(campaign.id, {
        campaign_id: campaign.id,
        publisher_id: Number(selectedPublisher),
        slot_id: selectedSlot || null
      });
      setSelectedPublisher("");
      setSelectedSlot("");
      const updated = await listCampaignAssignments(campaign.id);
      setAssignments(updated);
    } catch (err: any) {
      setError(err.message || "Failed to add assignment");
    } finally {
      setSubmitting(false);
    }
  };

  const confirmDelete = async () => {
    if (!assignmentToDelete) return;
    setDeleting(true);
    try {
      await deleteCampaignAssignment(campaign.id, assignmentToDelete.id);
      setAssignments((prev) => prev.filter((a) => a.id !== assignmentToDelete.id));
      setAssignmentToDelete(null);
    } catch (err: any) {
      setError(err.message || "Failed to remove assignment");
    } finally {
      setDeleting(false);
    }
  };

  const publisherSlots = slots.filter((s) => selectedPublisher && s.publisher_id === Number(selectedPublisher));

  return (
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
          maxWidth: "700px",
          width: "100%",
          padding: "24px",
          boxShadow: "var(--shadow-xl)",
          border: "1px solid var(--border-color)",
          maxHeight: "90vh",
          overflowY: "auto",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px", borderBottom: "1px solid var(--border-color)", paddingBottom: "12px" }}>
          <div>
            <h2 style={{ margin: 0, fontSize: "1.125rem", fontWeight: 700 }}>
              Assign Inventory: {campaign.name}
            </h2>
            <p style={{ margin: "4px 0 0", fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
              Lock this campaign exclusively to specific publisher properties or ad units.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onClose}
            aria-label="Close dialog"
            style={{ fontSize: "1.25rem", padding: "4px 8px" }}
          >
            ✕
          </button>
        </div>

        {error && (
          <Alert variant="danger" onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        {loading ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "10px", padding: "16px 0" }}>
            <LoadingSkeleton variant="rect" height={40} />
            <LoadingSkeleton variant="rect" height={40} />
          </div>
        ) : (
          <>
            <div style={{ marginBottom: "24px" }}>
              <h3 style={{ fontSize: "0.9375rem", fontWeight: 600, marginBottom: "12px" }}>
                Current Publisher Assignments ({assignments.length})
              </h3>
              {assignments.length === 0 ? (
                <div style={{ padding: "16px", background: "var(--color-bg-subtle)", borderRadius: "var(--radius-md)", color: "var(--color-text-muted)", fontSize: "0.875rem" }}>
                  No explicit publisher assignments. This campaign participates in global matching based on targeting.
                </div>
              ) : (
                <div className="table-container" style={{ border: "1px solid var(--border-color)", borderRadius: "var(--radius-md)", overflowX: "auto" }}>
                  <table className="table" style={{ margin: 0 }}>
                    <thead>
                      <tr>
                        <th>Publisher</th>
                        <th>Target Ad Slot</th>
                        <th style={{ textAlign: "right" }}>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {assignments.map((a) => {
                        const pub = publishers.find((p) => p.id === a.publisher_id);
                        const slot = slots.find((s) => s.slot_id === a.slot_id);
                        return (
                          <tr key={a.id}>
                            <td>
                              <div style={{ fontWeight: 600 }}>{pub ? pub.name : `#${a.publisher_id}`}</div>
                              {pub?.site_url && <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>{pub.site_url}</div>}
                            </td>
                            <td>
                              {a.slot_id ? (
                                <span>{slot ? `${slot.name} (${a.slot_id})` : a.slot_id}</span>
                              ) : (
                                <span style={{ color: "var(--color-text-muted)", fontStyle: "italic" }}>All Publisher Slots</span>
                              )}
                            </td>
                            <td style={{ textAlign: "right" }}>
                              <Button
                                variant="danger"
                                size="sm"
                                onClick={() => setAssignmentToDelete(a)}
                              >
                                Remove
                              </Button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div style={{ borderTop: "1px solid var(--border-color)", paddingTop: "16px" }}>
              <h3 style={{ fontSize: "0.9375rem", fontWeight: 600, marginBottom: "12px" }}>
                Add New Publisher Assignment
              </h3>
              <form onSubmit={handleAdd}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "16px" }}>
                  <div className="field">
                    <label htmlFor="assign-pub" style={{ fontWeight: 600, fontSize: "0.8125rem", marginBottom: "4px", display: "block" }}>
                      Publisher Account *
                    </label>
                    <select
                      id="assign-pub"
                      value={selectedPublisher}
                      onChange={(e) => {
                        setSelectedPublisher(e.target.value ? Number(e.target.value) : "");
                        setSelectedSlot("");
                      }}
                      className="select"
                      required
                    >
                      <option value="">[ Select Publisher ]</option>
                      {publishers.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} (ID #{p.id})
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="field">
                    <label htmlFor="assign-slot" style={{ fontWeight: 600, fontSize: "0.8125rem", marginBottom: "4px", display: "block" }}>
                      Specific Ad Slot (Optional)
                    </label>
                    <select
                      id="assign-slot"
                      value={selectedSlot}
                      onChange={(e) => setSelectedSlot(e.target.value)}
                      className="select"
                      disabled={!selectedPublisher}
                    >
                      <option value="">[ All Slots for this Publisher ]</option>
                      {publisherSlots.map((s) => (
                        <option key={s.slot_id} value={s.slot_id}>
                          {s.name} ({s.slot_id} · {s.width}×{s.height})
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                  <Button variant="secondary" onClick={onClose}>
                    Close
                  </Button>
                  <Button
                    type="submit"
                    variant="primary"
                    isLoading={submitting}
                    disabled={!selectedPublisher}
                  >
                    Assign Inventory
                  </Button>
                </div>
              </form>
            </div>
          </>
        )}
      </div>

      {/* Removal Confirmation Dialog */}
      <ConfirmDialog
        isOpen={!!assignmentToDelete}
        title="Remove Campaign Assignment"
        message="Are you sure you want to remove this publisher assignment? The campaign will no longer be locked to this inventory."
        confirmText="Remove Assignment"
        variant="danger"
        isLoading={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setAssignmentToDelete(null)}
      />
    </div>
  );
}
