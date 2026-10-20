import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { resendVerification } from "../api/client";

export default function VerificationBanner() {
  const { session, isVerified } = useAuth();
  const [resending, setResending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Only render for authenticated users whose email is not yet verified
  if (!session || isVerified) {
    return null;
  }

  const handleResend = async () => {
    if (resending || !session.user.email) return;
    setResending(true);
    setMessage(null);
    setError(null);
    try {
      const res = await resendVerification(session.user.email);
      setMessage(res.message || "A verification link has been sent to your email.");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to resend verification email.");
    } finally {
      setResending(false);
    }
  };

  return (
    <div
      role="region"
      aria-label="Email verification notice"
      className="verification-banner"
      style={{
        background: "#fffbeb",
        borderBottom: "1px solid #fde68a",
        padding: "10px 24px",
        display: "flex",
        flexWrap: "wrap",
        justifyContent: "space-between",
        alignItems: "center",
        gap: "12px",
        fontSize: "13px",
        color: "#92400e",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "8px", flex: "1 1 300px" }}>
        <span aria-hidden="true" style={{ fontSize: "16px" }}>⚠️</span>
        <div>
          <span>
            Please verify your email address (<strong>{session.user.email}</strong>) to unlock campaign creation, ad serving, and financial operations.
          </span>
          {message && (
            <div role="status" style={{ color: "#15803d", fontWeight: 500, marginTop: "2px" }}>
              ✓ {message}
            </div>
          )}
          {error && (
            <div role="alert" style={{ color: "#b91c1c", fontWeight: 500, marginTop: "2px" }}>
              ✕ {error}
            </div>
          )}
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <button
          type="button"
          className="btn secondary small"
          onClick={handleResend}
          disabled={resending}
          style={{ whiteSpace: "nowrap" }}
        >
          {resending ? "Sending..." : "Resend verification email"}
        </button>
      </div>
    </div>
  );
}
