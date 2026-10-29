import { useState, type FormEvent } from "react";
import { addContact } from "../../../api/client";
import type { EmailMarketingContact } from "../../../types";

interface AddContactModalProps {
  isOpen: boolean;
  listId: number;
  listName: string;
  onClose: () => void;
  onAdded: (newContact: EmailMarketingContact) => void;
}

const LAWFUL_SOURCES = [
  { value: "website_signup", label: "Website Newsletter Signup Form" },
  { value: "customer_opt_in", label: "Customer Checkout Affirmative Opt-in" },
  { value: "event_registration", label: "Event / Webinar Registration Opt-in" },
  { value: "inbound_inquiry", label: "Direct Inbound Contact Request" },
];

export default function AddContactModal({
  isOpen,
  listId,
  listName,
  onClose,
  onAdded,
}: AddContactModalProps) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [consentSource, setConsentSource] = useState(LAWFUL_SOURCES[0].value);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim()) {
      setError("Email address is required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const contact = await addContact(listId, {
        email: email.trim(),
        name: name.trim() || undefined,
        consent_source: consentSource,
      });
      setEmail("");
      setName("");
      onAdded(contact);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add contact.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="add-contact-title" data-testid="add-contact-modal">
      <div className="modal-dialog">
        <div className="modal-header">
          <h3 id="add-contact-title" className="modal-title">Add Contact to {listName}</h3>
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
              <label htmlFor="contact-email" style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                Email Address *
              </label>
              <input
                id="contact-email"
                type="email"
                className="input"
                style={{ width: "100%", padding: "8px", boxSizing: "border-box" }}
                placeholder="name@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                data-testid="input-contact-email"
              />
            </div>
            <div style={{ marginBottom: "12px" }}>
              <label htmlFor="contact-name" style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                Display Name (Optional)
              </label>
              <input
                id="contact-name"
                type="text"
                className="input"
                style={{ width: "100%", padding: "8px", boxSizing: "border-box" }}
                placeholder="e.g. Jane Doe"
                value={name}
                onChange={(e) => setName(e.target.value)}
                data-testid="input-contact-name"
              />
            </div>
            <div style={{ marginBottom: "14px" }}>
              <label htmlFor="contact-source" style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                Lawful Permission Source *
              </label>
              <select
                id="contact-source"
                className="input"
                style={{ width: "100%", padding: "8px" }}
                value={consentSource}
                onChange={(e) => setConsentSource(e.target.value)}
                data-testid="select-contact-source"
              >
                {LAWFUL_SOURCES.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
              <p style={{ margin: "4px 0 0", fontSize: "11px", color: "#64748b" }}>
                Anti-spam notice: Prohibited sources (scraped, purchased, or third-party co-registration) are rejected by policy.
              </p>
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn secondary small" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className="btn primary small" disabled={submitting || !email.trim()} data-testid="submit-add-contact">
              {submitting ? "Adding..." : "Add Contact"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

