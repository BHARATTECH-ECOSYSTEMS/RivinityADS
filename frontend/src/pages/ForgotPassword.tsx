import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { requestPasswordReset } from "../api/client";

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submittedEmail, setSubmittedEmail] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmedEmail = email.trim();
    if (busy || !trimmedEmail) return;

    setBusy(true);
    setError(null);

    try {
      await requestPasswordReset(trimmedEmail);
      setSubmittedEmail(trimmedEmail);
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to process password reset request. Please try again later."
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <div className="login-card">
        <h1>Forgot Password</h1>
        {submittedEmail ? (
          <div>
            <p role="status" className="success" style={{ marginBottom: "16px" }}>
              If an account exists with <strong>{submittedEmail}</strong>, password reset instructions have been sent.
            </p>
            <p className="muted" style={{ marginBottom: "16px" }}>
              Please check your inbox and follow the link to set a new password. The link will expire shortly.
            </p>
            <Link
              className="btn"
              style={{ display: "block", textAlign: "center" }}
              to="/login"
            >
              Return to Sign in
            </Link>
          </div>
        ) : (
          <>
            <p className="muted" style={{ marginBottom: "16px" }}>
              Enter your registered email address and we will send you a link to reset your password.
            </p>
            <form onSubmit={handleSubmit}>
              <div className="field">
                <label htmlFor="forgot-email">Email address *</label>
                <input
                  id="forgot-email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@example.com"
                  required
                  disabled={busy}
                />
              </div>

              {error && (
                <p role="alert" className="error" style={{ marginBottom: "12px" }}>
                  {error}
                </p>
              )}

              <button
                type="submit"
                className="btn"
                style={{ width: "100%", marginBottom: "14px" }}
                disabled={busy}
              >
                {busy ? "Sending reset link..." : "Send reset link"}
              </button>
            </form>
            <p className="muted">
              Remember your password? <Link to="/login">Sign in</Link>
            </p>
          </>
        )}
      </div>
    </div>
  );
}
