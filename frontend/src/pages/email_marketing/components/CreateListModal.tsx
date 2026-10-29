import { useState, type FormEvent } from "react";
import { createMailingList } from "../../../api/client";
import type { EmailMarketingList } from "../../../types";

interface CreateListModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated: (newList: EmailMarketingList) => void;
}

export default function CreateListModal({ isOpen, onClose, onCreated }: CreateListModalProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [type, setType] = useState<"public" | "private">("private");
  const [optin, setOptin] = useState<"single" | "double">("single");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("List name is required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const created = await createMailingList({
        name: name.trim(),
        description: description.trim() || undefined,
        type,
        optin,
      });
      setName("");
      setDescription("");
      setType("private");
      setOptin("single");
      onCreated(created);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create mailing list.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="create-list-modal-title" data-testid="create-list-modal">
      <div className="modal-dialog">
        <div className="modal-header">
          <h3 id="create-list-modal-title" className="modal-title">Create Mailing List</h3>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close dialog">
            <svg viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && (
              <div role="alert" style={{ background: "rgba(239, 68, 68, 0.1)", color: "#b91c1c", padding: "8px 12px", borderRadius: "4px", marginBottom: "12px", fontSize: "13px" }}>
                {error}
              </div>
            )}
            <div style={{ marginBottom: "12px" }}>
              <label htmlFor="list-name" style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                List Name *
              </label>
              <input
                id="list-name"
                type="text"
                className="input"
                style={{ width: "100%", padding: "8px", boxSizing: "border-box" }}
                placeholder="e.g. Product Newsletter, VIP Customers"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                data-testid="input-list-name"
              />
            </div>
            <div style={{ marginBottom: "12px" }}>
              <label htmlFor="list-desc" style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                Description (Optional)
              </label>
              <input
                id="list-desc"
                type="text"
                className="input"
                style={{ width: "100%", padding: "8px", boxSizing: "border-box" }}
                placeholder="Brief internal description of audience"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                data-testid="input-list-desc"
              />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "12px" }}>
              <div>
                <label htmlFor="list-type" style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                  List Visibility
                </label>
                <select
                  id="list-type"
                  className="input"
                  style={{ width: "100%", padding: "8px" }}
                  value={type}
                  onChange={(e) => setType(e.target.value as "public" | "private")}
                  data-testid="select-list-type"
                >
                  <option value="private">Private (Internal)</option>
                  <option value="public">Public (Opt-in)</option>
                </select>
              </div>
              <div>
                <label htmlFor="list-optin" style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                  Opt-in Mode
                </label>
                <select
                  id="list-optin"
                  className="input"
                  style={{ width: "100%", padding: "8px" }}
                  value={optin}
                  onChange={(e) => setOptin(e.target.value as "single" | "double")}
                  data-testid="select-list-optin"
                >
                  <option value="single">Single Opt-in</option>
                  <option value="double">Double Opt-in (Recommended)</option>
                </select>
              </div>
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn secondary small" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className="btn primary small" disabled={submitting || !name.trim()} data-testid="submit-create-list">
              {submitting ? "Creating..." : "Create List"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

