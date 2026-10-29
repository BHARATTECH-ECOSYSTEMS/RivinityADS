import { useState, useEffect, type FormEvent } from "react";
import { createMarketingCampaign, updateMarketingCampaign } from "../../../api/client";
import type { EmailMarketingCampaign, EmailMarketingList } from "../../../types";

interface CampaignEditorModalProps {
  isOpen: boolean;
  lists: EmailMarketingList[];
  campaign?: EmailMarketingCampaign | null;
  onClose: () => void;
  onSaved: (campaign: EmailMarketingCampaign) => void;
}

export default function CampaignEditorModal({
  isOpen,
  lists,
  campaign,
  onClose,
  onSaved,
}: CampaignEditorModalProps) {
  const [listId, setListId] = useState<number>(lists[0]?.id || 0);
  const [name, setName] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [contentType, setContentType] = useState<"html" | "markdown" | "plain">("html");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (campaign) {
      setListId(campaign.list_id);
      setName(campaign.name);
      setSubject(campaign.subject);
      setBody(campaign.body);
      setContentType((campaign.content_type as "html" | "markdown" | "plain") || "html");
    } else {
      setListId(lists[0]?.id || 0);
      setName("");
      setSubject("");
      setBody("<h2>Special Announcement</h2>\n<p>Hello valued subscriber,</p>\n<p>Here is our latest update.</p>\n<p><a href=\"{{ UnsubscribeURL }}\">Unsubscribe from this list</a></p>");
      setContentType("html");
    }
  }, [campaign, lists]);

  if (!isOpen) return null;

  const isEditing = Boolean(campaign);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !subject.trim() || !body.trim()) {
      setError("Name, subject, and message body are required.");
      return;
    }
    if (!listId && !isEditing) {
      setError("Please select a target mailing list.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      let saved: EmailMarketingCampaign;
      if (isEditing && campaign) {
        saved = await updateMarketingCampaign(campaign.id, {
          name: name.trim(),
          subject: subject.trim(),
          body: body.trim(),
          content_type: contentType,
        });
      } else {
        saved = await createMarketingCampaign({
          list_id: Number(listId),
          name: name.trim(),
          subject: subject.trim(),
          body: body.trim(),
          content_type: contentType,
        });
      }
      onSaved(saved);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save campaign.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="campaign-editor-title" data-testid="campaign-editor-modal">
      <div className="modal-dialog" style={{ maxWidth: "600px" }}>
        <div className="modal-header">
          <h3 id="campaign-editor-title" className="modal-title">
            {isEditing ? `Edit Draft: ${campaign?.name}` : "Create New Marketing Campaign"}
          </h3>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close dialog">
            <svg viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body" style={{ maxHeight: "70vh", overflowY: "auto" }}>
            {error && (
              <div role="alert" style={{ background: "rgba(239, 68, 68, 0.1)", color: "#b91c1c", padding: "8px 12px", borderRadius: "4px", marginBottom: "12px", fontSize: "13px" }}>
                {error}
              </div>
            )}

            {!isEditing && (
              <div style={{ marginBottom: "12px" }}>
                <label htmlFor="camp-list" style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                  Target Contact List *
                </label>
                <select
                  id="camp-list"
                  className="input"
                  style={{ width: "100%", padding: "8px" }}
                  value={listId}
                  onChange={(e) => setListId(Number(e.target.value))}
                  required
                  data-testid="select-campaign-list"
                >
                  {lists.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.name} ({l.contact_count} subscribers)
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div style={{ marginBottom: "12px" }}>
              <label htmlFor="camp-name" style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                Campaign Internal Name *
              </label>
              <input
                id="camp-name"
                type="text"
                className="input"
                style={{ width: "100%", padding: "8px", boxSizing: "border-box" }}
                placeholder="e.g. November Product Launch"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                data-testid="input-campaign-name"
              />
            </div>

            <div style={{ marginBottom: "12px" }}>
              <label htmlFor="camp-subject" style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                Email Subject Line *
              </label>
              <input
                id="camp-subject"
                type="text"
                className="input"
                style={{ width: "100%", padding: "8px", boxSizing: "border-box" }}
                placeholder="e.g. Exciting New Features Are Here!"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                required
                data-testid="input-campaign-subject"
              />
            </div>

            <div style={{ marginBottom: "12px" }}>
              <label htmlFor="camp-type" style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                Content Format
              </label>
              <select
                id="camp-type"
                className="input"
                style={{ width: "100%", padding: "8px" }}
                value={contentType}
                onChange={(e) => setContentType(e.target.value as "html" | "markdown" | "plain")}
                data-testid="select-campaign-content-type"
              >
                <option value="html">HTML</option>
                <option value="plain">Plain Text</option>
                <option value="markdown">Markdown</option>
              </select>
            </div>

            <div style={{ marginBottom: "12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                <label htmlFor="camp-body" style={{ fontSize: "13px", fontWeight: 600 }}>
                  Message Body *
                </label>
                <span style={{ fontSize: "11px", color: "#64748b" }}>
                  Tag: <code>{"{{ UnsubscribeURL }}"}</code>
                </span>
              </div>
              <textarea
                id="camp-body"
                className="input"
                rows={8}
                style={{ width: "100%", padding: "8px", fontFamily: "monospace", fontSize: "12px", boxSizing: "border-box" }}
                value={body}
                onChange={(e) => setBody(e.target.value)}
                required
                data-testid="textarea-campaign-body"
              />
              <p style={{ margin: "4px 0 0", fontSize: "11px", color: "#64748b" }}>
                Policy requirement: A mandatory unsubscribe footer is automatically appended by the server if not explicitly placed.
              </p>
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn secondary small" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn primary small"
              disabled={submitting || !name.trim() || !subject.trim() || !body.trim()}
              data-testid="submit-save-campaign"
            >
              {submitting ? "Saving..." : isEditing ? "Save Changes" : "Create Draft"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

