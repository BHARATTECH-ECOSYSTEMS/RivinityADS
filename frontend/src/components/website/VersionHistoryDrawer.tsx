import React, { useState, useEffect } from "react";
import { VersionSummary } from "../../types";
import { listWebsiteVersions } from "../../api/client";

interface VersionHistoryDrawerProps {
  isOpen: boolean;
  projectId: number;
  currentPublishedVersionId?: number | null;
  onClose: () => void;
  onRollback: (versionId: number) => Promise<void>;
}

export const VersionHistoryDrawer: React.FC<VersionHistoryDrawerProps> = ({
  isOpen,
  projectId,
  currentPublishedVersionId,
  onClose,
  onRollback,
}) => {
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedRollbackId, setSelectedRollbackId] = useState<number | null>(null);
  const [isRollingBack, setIsRollingBack] = useState(false);

  useEffect(() => {
    if (isOpen && projectId) {
      loadVersions();
    } else {
      setSelectedRollbackId(null);
      setError(null);
    }
  }, [isOpen, projectId]);

  const loadVersions = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await listWebsiteVersions(projectId);
      setVersions(data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || "Failed to load publication versions");
    } finally {
      setIsLoading(false);
    }
  };

  const handleConfirmRollback = async (versionId: number) => {
    setIsRollingBack(true);
    setError(null);
    try {
      await onRollback(versionId);
      setSelectedRollbackId(null);
      await loadVersions();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || "Rollback failed");
    } finally {
      setIsRollingBack(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        backgroundColor: "rgba(0, 0, 0, 0.4)",
        display: "flex",
        justifyContent: "flex-end",
        backdropFilter: "blur(2px)",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && !isRollingBack) onClose();
      }}
      data-testid="version-history-drawer"
    >
      <div
        className="card"
        style={{
          width: "100%",
          maxWidth: "420px",
          height: "100%",
          borderRadius: 0,
          borderLeft: "1px solid var(--border)",
          boxShadow: "-8px 0 25px rgba(0, 0, 0, 0.2)",
          display: "flex",
          flexDirection: "column",
          padding: "1.5rem",
          overflowY: "auto",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.25rem" }}>
          <div>
            <h2 style={{ fontSize: "1.125rem", fontWeight: 700, margin: 0 }}>Version History</h2>
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
              Publication snapshots & rollback
            </div>
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onClose}
            disabled={isRollingBack}
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
          >
            {error}
          </div>
        )}

        {isLoading ? (
          <div style={{ textAlign: "center", padding: "2rem", color: "var(--text-muted)", fontSize: "0.875rem" }}>
            Loading version history...
          </div>
        ) : versions.length === 0 ? (
          <div
            style={{
              textAlign: "center",
              padding: "3rem 1rem",
              color: "var(--text-muted)",
              background: "var(--bg-secondary)",
              borderRadius: "var(--radius-md)",
            }}
          >
            <div style={{ fontSize: "1.5rem", marginBottom: "0.5rem" }}>📦</div>
            <div style={{ fontWeight: 600, fontSize: "0.875rem", marginBottom: "0.25rem" }}>
              No Published Versions Yet
            </div>
            <div style={{ fontSize: "0.75rem", lineHeight: 1.4 }}>
              When you publish your site, an immutable snapshot will be recorded here for instant rollback.
            </div>
          </div>
        ) : (
          <div className="space-y-3" style={{ flex: 1 }}>
            {versions.map((ver) => {
              const isCurrent = ver.id === currentPublishedVersionId;
              const isSelectedForRollback = selectedRollbackId === ver.id;
              const dateStr = new Date(ver.published_at).toLocaleString();

              return (
                <div
                  key={ver.id}
                  style={{
                    padding: "0.875rem",
                    border: "1px solid",
                    borderColor: isCurrent ? "var(--primary)" : "var(--border)",
                    borderRadius: "var(--radius-md)",
                    backgroundColor: isCurrent ? "rgba(37, 99, 235, 0.04)" : "var(--bg-card)",
                    transition: "all 0.15s ease",
                  }}
                  data-testid={`version-item-${ver.version_number}`}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.375rem" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      <span style={{ fontWeight: 700, fontSize: "0.9375rem" }}>
                        v{ver.version_number}
                      </span>
                      {isCurrent && (
                        <span
                          className="wb-badge wb-badge-published"
                          style={{ fontSize: "0.625rem", padding: "0.15rem 0.4rem" }}
                        >
                          Active Public Version
                        </span>
                      )}
                    </div>
                    <span style={{ fontSize: "0.6875rem", color: "var(--text-muted)" }}>
                      #{ver.id}
                    </span>
                  </div>

                  <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: "0.5rem" }}>
                    {dateStr}
                  </div>

                  {ver.notes && (
                    <div
                      style={{
                        fontSize: "0.8125rem",
                        color: "var(--text-secondary)",
                        marginBottom: "0.625rem",
                        padding: "0.375rem 0.5rem",
                        background: "var(--bg-secondary)",
                        borderRadius: "var(--radius-sm)",
                        fontStyle: "italic",
                      }}
                    >
                      "{ver.notes}"
                    </div>
                  )}

                  {!isSelectedForRollback ? (
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => setSelectedRollbackId(ver.id)}
                      disabled={isRollingBack}
                      style={{ fontSize: "0.75rem", padding: "0.25rem 0.5rem", width: "100%" }}
                      data-testid={`start-rollback-v${ver.version_number}`}
                    >
                      Rollback to v{ver.version_number}
                    </button>
                  ) : (
                    <div
                      style={{
                        padding: "0.625rem",
                        borderRadius: "var(--radius-sm)",
                        backgroundColor: "rgba(245, 158, 11, 0.08)",
                        border: "1px solid rgba(245, 158, 11, 0.3)",
                        marginTop: "0.5rem",
                      }}
                    >
                      <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginBottom: "0.5rem" }}>
                        ⚠️ Overwrite current draft and restore pages & blocks from v{ver.version_number}?
                      </div>
                      <div style={{ display: "flex", gap: "0.375rem" }}>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => setSelectedRollbackId(null)}
                          disabled={isRollingBack}
                          style={{ fontSize: "0.6875rem", flex: 1 }}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="btn btn-primary btn-sm"
                          onClick={() => handleConfirmRollback(ver.id)}
                          disabled={isRollingBack}
                          style={{ fontSize: "0.6875rem", flex: 1 }}
                          data-testid={`confirm-rollback-v${ver.version_number}`}
                        >
                          {isRollingBack ? "Restoring..." : "Confirm Rollback"}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
