import { useState, useMemo, type ChangeEvent } from "react";
import { importContactsCsv } from "../../../api/client";
import type { ImportContactsResult } from "../../../types";

interface CsvImportModalProps {
  isOpen: boolean;
  listId: number;
  listName: string;
  onClose: () => void;
  onImportComplete: (result: ImportContactsResult) => void;
}

const LAWFUL_SOURCES = [
  { value: "website_signup", label: "Website Newsletter Signup Form" },
  { value: "customer_opt_in", label: "Customer Checkout Affirmative Opt-in" },
  { value: "event_registration", label: "Event / Webinar Registration Opt-in" },
  { value: "inbound_inquiry", label: "Direct Inbound Contact Request" },
];

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface ParsedRow {
  email: string;
  name: string;
  isValid: boolean;
  isDuplicate: boolean;
  raw: string;
}

export default function CsvImportModal({
  isOpen,
  listId,
  listName,
  onClose,
  onImportComplete,
}: CsvImportModalProps) {
  const [csvText, setCsvText] = useState("");
  const [permissionSource, setPermissionSource] = useState(LAWFUL_SOURCES[0].value);
  const [confirmedPolicy, setConfirmedPolicy] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importResult, setImportResult] = useState<ImportContactsResult | null>(null);

  // Parse CSV client-side for preflight preview
  const preflight = useMemo(() => {
    if (!csvText.trim()) {
      return { headers: [], rows: [], validCount: 0, invalidCount: 0, duplicateCount: 0 };
    }

    const lines = csvText.split(/\r?\n/).map((l) => l.trim()).filter((l) => l.length > 0);
    if (lines.length === 0) {
      return { headers: [], rows: [], validCount: 0, invalidCount: 0, duplicateCount: 0 };
    }

    // Header extraction
    const rawHeaders = lines[0].split(",").map((h) => h.trim().toLowerCase().replace(/['"]/g, ""));
    const emailIndex = rawHeaders.findIndex((h) => h.includes("email") || h === "mail");
    const nameIndex = rawHeaders.findIndex((h) => h.includes("name") || h === "user");

    const emailCol = emailIndex >= 0 ? emailIndex : 0;
    const nameCol = nameIndex >= 0 ? nameIndex : 1;

    const dataLines = lines.slice(1);
    const seenEmails = new Set<string>();
    let validCount = 0;
    let invalidCount = 0;
    let duplicateCount = 0;

    const parsedRows: ParsedRow[] = dataLines.map((line) => {
      const parts = line.split(",").map((p) => p.trim().replace(/^['"]|['"]$/g, ""));
      const email = parts[emailCol] || "";
      const name = parts[nameCol] || "";

      const isValid = EMAIL_REGEX.test(email);
      const normalized = email.toLowerCase();
      const isDuplicate = seenEmails.has(normalized);

      if (isDuplicate) {
        duplicateCount++;
      } else if (!isValid) {
        invalidCount++;
      } else {
        validCount++;
        seenEmails.add(normalized);
      }

      return {
        email,
        name,
        isValid,
        isDuplicate,
        raw: line,
      };
    });

    return {
      headers: rawHeaders,
      rows: parsedRows,
      validCount,
      invalidCount,
      duplicateCount,
    };
  }, [csvText]);

  if (!isOpen) return null;

  const handleFileUpload = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (event) => {
      const text = event.target?.result as string;
      if (text) setCsvText(text);
    };
    reader.readAsText(file);
  };

  const handleImport = async () => {
    if (!confirmedPolicy || preflight.validCount === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await importContactsCsv(listId, {
        csv_content: csvText.trim(),
        permission_source: permissionSource,
      });
      setImportResult(result);
      onImportComplete(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "CSV import failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="csv-import-title" data-testid="csv-import-modal">
      <div className="modal-dialog" style={{ maxWidth: "620px" }}>
        <div className="modal-header">
          <h3 id="csv-import-title" className="modal-title">Import Contacts to {listName}</h3>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close dialog">
            <svg viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <div className="modal-body" style={{ maxHeight: "75vh", overflowY: "auto" }}>
          {error && (
            <div role="alert" style={{ background: "rgba(239, 68, 68, 0.1)", color: "#b91c1c", padding: "10px 14px", borderRadius: "4px", marginBottom: "14px", fontSize: "13px" }}>
              <strong>Import Error:</strong> {error}
            </div>
          )}

          {importResult ? (
            <div data-testid="import-result-summary" style={{ textAlign: "center", padding: "16px 0" }}>
              <div style={{ fontSize: "20px", fontWeight: 700, color: "#16a34a", marginBottom: "8px" }}>
                Import Completed Successfully
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "8px", margin: "16px 0" }}>
                <div style={{ background: "#f8fafc", padding: "10px", borderRadius: "6px" }}>
                  <div style={{ fontSize: "11px", color: "#64748b" }}>TOTAL ROWS</div>
                  <div style={{ fontSize: "18px", fontWeight: 700 }}>{importResult.total_rows}</div>
                </div>
                <div style={{ background: "#ecfdf5", padding: "10px", borderRadius: "6px" }}>
                  <div style={{ fontSize: "11px", color: "#15803d" }}>IMPORTED</div>
                  <div style={{ fontSize: "18px", fontWeight: 700, color: "#15803d" }}>{importResult.valid_count}</div>
                </div>
                <div style={{ background: "#fef3c7", padding: "10px", borderRadius: "6px" }}>
                  <div style={{ fontSize: "11px", color: "#b45309" }}>DUPLICATES</div>
                  <div style={{ fontSize: "18px", fontWeight: 700, color: "#b45309" }}>{importResult.duplicate_count}</div>
                </div>
                <div style={{ background: "#fef2f2", padding: "10px", borderRadius: "6px" }}>
                  <div style={{ fontSize: "11px", color: "#b91c1c" }}>ERRORS</div>
                  <div style={{ fontSize: "18px", fontWeight: 700, color: "#b91c1c" }}>{importResult.error_count}</div>
                </div>
              </div>
              {importResult.errors && importResult.errors.length > 0 && (
                <div style={{ textAlign: "left", background: "#fef2f2", padding: "10px", borderRadius: "4px", fontSize: "12px", color: "#991b1b" }}>
                  <strong>Errors reported by server:</strong>
                  <ul style={{ margin: "4px 0 0", paddingLeft: "18px" }}>
                    {importResult.errors.map((e, idx) => <li key={idx}>{e}</li>)}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <>
              {/* Consent Source Selection */}
              <div style={{ marginBottom: "14px" }}>
                <label htmlFor="csv-permission-source" style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}>
                  Lawful Permission Source *
                </label>
                <select
                  id="csv-permission-source"
                  className="input"
                  style={{ width: "100%", padding: "8px" }}
                  value={permissionSource}
                  onChange={(e) => setPermissionSource(e.target.value)}
                  data-testid="select-permission-source"
                >
                  {LAWFUL_SOURCES.map((s) => (
                    <option key={s.value} value={s.value}>{s.label}</option>
                  ))}
                </select>
              </div>

              {/* CSV Input: File or Paste */}
              <div style={{ marginBottom: "14px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                  <label htmlFor="csv-text-input" style={{ fontSize: "13px", fontWeight: 600 }}>
                    CSV Data (Header: <code>email, name</code>) *
                  </label>
                  <label style={{ fontSize: "12px", color: "#2563eb", cursor: "pointer" }}>
                    Choose file (.csv)
                    <input type="file" accept=".csv,text/csv" onChange={handleFileUpload} style={{ display: "none" }} />
                  </label>
                </div>
                <textarea
                  id="csv-text-input"
                  className="input"
                  rows={5}
                  style={{ width: "100%", padding: "8px", fontFamily: "monospace", fontSize: "12px", boxSizing: "border-box" }}
                  placeholder="email,name&#10;alice@example.com,Alice Cooper&#10;bob@example.com,Bob Marley"
                  value={csvText}
                  onChange={(e) => setCsvText(e.target.value)}
                  data-testid="textarea-csv-content"
                />
              </div>

              {/* Preflight Summary & Preview */}
              {preflight.rows.length > 0 && (
                <div style={{ background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: "6px", padding: "12px", marginBottom: "14px" }} data-testid="preflight-summary">
                  <div style={{ fontWeight: 600, fontSize: "13px", marginBottom: "8px" }}>
                    Preflight Validation Summary
                  </div>
                  <div style={{ display: "flex", gap: "16px", fontSize: "12px", marginBottom: "10px" }}>
                    <span>Total Rows: <strong>{preflight.rows.length}</strong></span>
                    <span style={{ color: "#16a34a" }}>Valid: <strong>{preflight.validCount}</strong></span>
                    {preflight.invalidCount > 0 && <span style={{ color: "#dc2626" }}>Invalid Syntax: <strong>{preflight.invalidCount}</strong></span>}
                    {preflight.duplicateCount > 0 && <span style={{ color: "#d97706" }}>Duplicates in File: <strong>{preflight.duplicateCount}</strong></span>}
                  </div>

                  <div style={{ fontSize: "11px", fontWeight: 600, color: "#64748b", textTransform: "uppercase", marginBottom: "4px" }}>
                    Sample Rows Preview (First 5):
                  </div>
                  <div style={{ maxHeight: "140px", overflowY: "auto", border: "1px solid #e2e8f0", borderRadius: "4px" }}>
                    <table style={{ width: "100%", fontSize: "11px", borderCollapse: "collapse" }}>
                      <thead>
                        <tr style={{ background: "#f1f5f9", textAlign: "left" }}>
                          <th style={{ padding: "4px 8px" }}>Email</th>
                          <th style={{ padding: "4px 8px" }}>Name</th>
                          <th style={{ padding: "4px 8px" }}>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preflight.rows.slice(0, 5).map((r, i) => (
                          <tr key={i} style={{ borderTop: "1px solid #f1f5f9" }}>
                            <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>{r.email || "<missing>"}</td>
                            <td style={{ padding: "4px 8px" }}>{r.name || "-"}</td>
                            <td style={{ padding: "4px 8px" }}>
                              {r.isDuplicate ? (
                                <span style={{ color: "#b45309", background: "#fef3c7", padding: "1px 4px", borderRadius: "2px" }}>Duplicate</span>
                              ) : r.isValid ? (
                                <span style={{ color: "#15803d", background: "#dcfce7", padding: "1px 4px", borderRadius: "2px" }}>Valid</span>
                              ) : (
                                <span style={{ color: "#b91c1c", background: "#fee2e2", padding: "1px 4px", borderRadius: "2px" }}>Invalid</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Acceptable Use Declaration */}
              <div style={{ borderTop: "1px solid #e2e8f0", paddingTop: "12px", marginTop: "12px" }}>
                <label style={{ display: "flex", alignItems: "flex-start", gap: "8px", fontSize: "12px", color: "#334155", cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={confirmedPolicy}
                    onChange={(e) => setConfirmedPolicy(e.target.checked)}
                    data-testid="checkbox-confirm-policy"
                    style={{ marginTop: "2px" }}
                  />
                  <span>
                    <strong>Anti-Spam Affirmation:</strong> I certify that all contacts in this upload gave direct, affirmative consent to receive email marketing from this tenant. I understand purchased, scraped, or unconsented lists are strictly forbidden.
                  </span>
                </label>
              </div>
            </>
          )}
        </div>

        <div className="modal-footer">
          {importResult ? (
            <button type="button" className="btn primary small" onClick={onClose} data-testid="btn-close-result">
              Done
            </button>
          ) : (
            <>
              <button type="button" className="btn secondary small" onClick={onClose} disabled={submitting}>
                Cancel
              </button>
              <button
                type="button"
                className="btn primary small"
                onClick={handleImport}
                disabled={submitting || !confirmedPolicy || preflight.validCount === 0}
                data-testid="submit-csv-import"
              >
                {submitting ? "Importing..." : `Import ${preflight.validCount} Contacts`}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

