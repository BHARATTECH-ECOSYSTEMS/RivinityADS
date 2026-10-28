import React, { useState } from "react";
import { ProjectData } from "../../types";

interface PublishModalProps {
  isOpen: boolean;
  project: ProjectData;
  isPublishing: boolean;
  isUnpublishing: boolean;
  error: string | null;
  onClose: () => void;
  onPublish: (changelogNotes?: string) => Promise<void>;
  onUnpublish: () => Promise<void>;
}

export const PublishModal: React.FC<PublishModalProps> = ({
  isOpen,
  project,
  isPublishing,
  isUnpublishing,
  error,
  onClose,
  onPublish,
  onUnpublish,
}) => {
  const [changelogNotes, setChangelogNotes] = useState("");
  const [confirmUnpublish, setConfirmUnpublish] = useState(false);

  if (!isOpen) return null;

  const isPublished = project.status === 1;

  const handlePublish = async (e: React.FormEvent) => {
    e.preventDefault();
    await onPublish(changelogNotes.trim() || undefined);
    setChangelogNotes("");
  };

  const handleUnpublish = async () => {
    await onUnpublish();
    setConfirmUnpublish(false);
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        backgroundColor: "rgba(0, 0, 0, 0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "1rem",
        backdropFilter: "blur(2px)",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && !isPublishing && !isUnpublishing) {
          onClose();
        }
      }}
      data-testid="publish-modal"
    >
      <div
        className="card"
        style={{
          width: "100%",
          maxWidth: "520px",
          padding: "1.75rem",
          boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.2)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.25rem" }}>
          <div>
            <h2 style={{ fontSize: "1.25rem", fontWeight: 700, margin: 0 }}>
              {isPublished ? "Website Publication Status" : "Publish Website"}
            </h2>
            <div style={{ fontSize: "0.8125rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
              Slug: <code style={{ color: "var(--primary)" }}>{project.slug}</code>
            </div>
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onClose}
            disabled={isPublishing || isUnpublishing}
            style={{ fontSize: "0.875rem", padding: "0.25rem 0.5rem" }}
          >
            ✕
          </button>
        </div>

        {error && (
          <div
            style={{
              padding: "0.75rem",
              borderRadius: "var(--radius-sm)",
              backgroundColor: "rgba(239, 68, 68, 0.1)",
              border: "1px solid rgba(239, 68, 68, 0.3)",
              color: "var(--error)",
              fontSize: "0.8125rem",
              marginBottom: "1rem",
            }}
            data-testid="publish-error-alert"
          >
            {error}
          </div>
        )}

        {isPublished && (
          <div
            style={{
              padding: "1rem",
              borderRadius: "var(--radius-md)",
              backgroundColor: "rgba(16, 185, 129, 0.08)",
              border: "1px solid rgba(16, 185, 129, 0.3)",
              marginBottom: "1.25rem",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem" }}>
              <span style={{ fontSize: "1.125rem" }}>🟢</span>
              <strong style={{ color: "var(--success)", fontSize: "0.9375rem" }}>
                Website is currently Live & Published
              </strong>
            </div>
            {project.published_url && (
              <div style={{ fontSize: "0.8125rem", wordBreak: "break-all" }}>
                Public URL:{" "}
                <a
                  href={project.published_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: "var(--primary)", fontWeight: 600, textDecoration: "underline" }}
                  data-testid="published-site-url"
                >
                  {project.published_url}
                </a>
              </div>
            )}
            {project.current_published_version_id && (
              <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
                Active Published Snapshot ID: #{project.current_published_version_id}
              </div>
            )}
          </div>
        )}

        <form onSubmit={handlePublish} className="space-y-3">
          <div>
            <label className="form-label" style={{ fontSize: "0.8125rem" }}>
              Publication Changelog Notes (Optional)
            </label>
            <input
              type="text"
              className="form-input"
              value={changelogNotes}
              placeholder="e.g. Updated home hero headline and pricing features"
              maxLength={255}
              onChange={(e) => setChangelogNotes(e.target.value)}
              disabled={isPublishing || isUnpublishing}
              data-testid="publish-changelog-input"
            />
            <span style={{ fontSize: "0.6875rem", color: "var(--text-muted)" }}>
              A permanent, immutable snapshot will be created in your version history.
            </span>
          </div>

          <div style={{ display: "flex", gap: "0.75rem", justifyContent: "flex-end", marginTop: "1.25rem" }}>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={onClose}
              disabled={isPublishing || isUnpublishing}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={isPublishing || isUnpublishing}
              data-testid="confirm-publish-btn"
            >
              {isPublishing ? "Publishing Snapshot..." : isPublished ? "Publish Updates (New Version)" : "Publish Website"}
            </button>
          </div>
        </form>

        {isPublished && (
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: "1rem", marginTop: "1.25rem" }}>
            {!confirmUnpublish ? (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setConfirmUnpublish(true)}
                disabled={isPublishing || isUnpublishing}
                style={{ width: "100%", color: "var(--error)", fontSize: "0.8125rem" }}
                data-testid="start-unpublish-btn"
              >
                Unpublish Website (Take Offline)
              </button>
            ) : (
              <div style={{ textAlign: "center", padding: "0.75rem", background: "rgba(239, 68, 68, 0.08)", borderRadius: "var(--radius-sm)" }}>
                <div style={{ fontSize: "0.8125rem", color: "var(--error)", marginBottom: "0.5rem" }}>
                  Are you sure you want to take this website offline? Visitors will receive a 404 until you publish again.
                </div>
                <div style={{ display: "flex", gap: "0.5rem", justifyContent: "center" }}>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => setConfirmUnpublish(false)}
                    disabled={isUnpublishing}
                  >
                    Keep Online
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={handleUnpublish}
                    disabled={isUnpublishing}
                    style={{ background: "var(--error)", borderColor: "var(--error)" }}
                    data-testid="confirm-unpublish-btn"
                  >
                    {isUnpublishing ? "Taking Offline..." : "Confirm Unpublish"}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
