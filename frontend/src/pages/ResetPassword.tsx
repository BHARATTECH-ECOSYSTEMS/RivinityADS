import { useState, useEffect, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { resetPassword } from "../api/client";

export default function ResetPassword() {
  const [searchParams] = useSearchParams();
  // Capture reset token in component memory immediately on initial mount
  const [token] = useState<string | null>(() => searchParams.get("token"));

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(
    token ? null : "Missing password reset token. Please use the link sent to your email."
  );
  const [success, setSuccess] = useState(false);

  // Clean sensitive reset token from browser visible URL and history entry as early as possible
  useEffect(() => {
    if (typeof window !== "undefined" && window.location?.search?.includes("token=")) {
      try {
        const url = new URL(window.location.href);
        if (url.searchParams.has("token")) {
          url.searchParams.delete("token");
          const cleanSearch = url.searchParams.toString();
          const cleanUrl = url.pathname + (cleanSearch ? `?${cleanSearch}` : "") + url.hash;
          window.history.replaceState(window.history.state, document.title, cleanUrl);
        }
      } catch {
        // Safe fallback if URL parsing fails
      }
    }
  }, []);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;

    if (!token) {
      setError("No reset token provided. Please use the link from your email.");
      return;
    }

    if (password.length < 12) {
      setError("Password must be at least 12 characters.");
      return;
    }

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setBusy(true);
    setError(null);

    try {
      await resetPassword({ token, password });
      setSuccess(true);
      setPassword("");
      setConfirmPassword("");
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : "Password reset failed. The link may have expired or already been used."
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <div className="login-card">
        <h1>Reset Password</h1>
        {success ? (
          <div>
            <p role="status" className="success" style={{ marginBottom: "16px" }}>
              ✓ Your password has been successfully reset!
            </p>
            <p className="muted" style={{ marginBottom: "16px" }}>
              You can now sign in to your account using your new password.
            </p>
            <Link
              className="btn"
              style={{ display: "block", textAlign: "center" }}
              to="/login"
            >
              Sign in
            </Link>
          </div>
        ) : !token ? (
          <div role="alert">
            <p className="error" style={{ marginBottom: "16px" }}>
              ✕ Missing password reset token.
            </p>
            <p className="muted" style={{ marginBottom: "16px" }}>
              Please check the link provided in your email or request a new password reset link.
            </p>
            <Link
              className="btn"
              style={{ display: "block", textAlign: "center" }}
              to="/forgot-password"
            >
              Request new reset link
            </Link>
          </div>
        ) : (
          <>
            <p className="muted" style={{ marginBottom: "16px" }}>
              Create a new secure password for your account.
            </p>
            <form onSubmit={handleSubmit}>
              <div className="field">
                <label htmlFor="reset-new-password">New password *</label>
                <input
                  id="reset-new-password"
                  type="password"
                  autoComplete="new-password"
                  minLength={12}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="At least 12 characters"
                  required
                  disabled={busy}
                />
                <span className="muted">Use at least 12 characters.</span>
              </div>

              <div className="field">
                <label htmlFor="reset-confirm-password">Confirm new password *</label>
                <input
                  id="reset-confirm-password"
                  type="password"
                  autoComplete="new-password"
                  minLength={12}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="Repeat your new password"
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
                {busy ? "Resetting password..." : "Reset password"}
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
