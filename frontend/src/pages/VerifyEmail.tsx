import { useEffect, useState, useRef } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth, roleHome } from "../auth/AuthContext";
import { verifyEmail } from "../api/client";
import type { VerifyEmailResponse } from "../types";

export default function VerifyEmail() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  // Capture token in component memory immediately on initial mount
  const [token] = useState<string | null>(() => searchParams.get("token"));
  const { session, refreshUser, logout } = useAuth();

  const [status, setStatus] = useState<"idle" | "verifying" | "success" | "error">(
    token ? "verifying" : "idle"
  );
  const [message, setMessage] = useState<string | null>(null);
  const [verifiedEmail, setVerifiedEmail] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(
    token ? null : "No verification token provided. Please check the link in your email."
  );
  const attemptedRef = useRef(false);

  // Clean sensitive token parameter from browser visible URL and history entry
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

  useEffect(() => {
    if (!token || attemptedRef.current) return;
    attemptedRef.current = true;

    let isMounted = true;
    (async () => {
      try {
        const res: VerifyEmailResponse = await verifyEmail(token);
        if (!isMounted) return;
        setMessage(res.message || "Your email address has been successfully verified.");
        setVerifiedEmail(res.email || null);
        setStatus("success");

        // F-04: Cross-account verification handling
        // Only refresh active user session if verified email matches the current session
        if (session && (!res.email || session.user.email.toLowerCase() === res.email.toLowerCase())) {
          try {
            await refreshUser();
          } catch {
            // Transient refresh error does not break verification success
          }
        }
      } catch (err: unknown) {
        if (!isMounted) return;
        setStatus("error");
        setError(
          err instanceof Error
            ? err.message
            : "Verification failed. The token may be invalid or expired."
        );
      }
    })();

    return () => {
      isMounted = false;
    };
  }, [token, session, refreshUser]);

  const handleSwitchAccount = async () => {
    await logout();
    navigate("/login");
  };

  const isCrossAccount = Boolean(
    session && verifiedEmail && session.user.email.toLowerCase() !== verifiedEmail.toLowerCase()
  );

  return (
    <div className="login-wrap">
      <div className="login-card">
        <h1>Email Verification</h1>
        {status === "verifying" && (
          <div role="status">
            <p className="muted" style={{ marginBottom: "16px" }}>
              Verifying your email address, please wait...
            </p>
          </div>
        )}

        {status === "success" && (
          <div role="status">
            <p className="success" style={{ marginBottom: "16px" }}>
              ✓ {message || "Your email address has been successfully verified."}
            </p>

            {isCrossAccount ? (
              <div>
                <p className="muted" style={{ marginBottom: "12px" }}>
                  Verified email: <strong>{verifiedEmail}</strong>
                </p>
                <div
                  style={{
                    background: "rgba(234, 179, 8, 0.1)",
                    border: "1px solid rgba(234, 179, 8, 0.3)",
                    borderRadius: "6px",
                    padding: "12px",
                    marginBottom: "16px",
                  }}
                >
                  <p style={{ margin: 0, fontSize: "0.875rem", color: "#facc15" }}>
                    Notice: You are currently signed in as <strong>{session?.user.email}</strong>.
                    The email address that was just verified belongs to a different account.
                  </p>
                </div>
                <div style={{ display: "flex", gap: "10px" }}>
                  <button
                    type="button"
                    className="btn"
                    style={{ flex: 1, textAlign: "center" }}
                    onClick={handleSwitchAccount}
                  >
                    Switch Account
                  </button>
                  <Link
                    className="btn btn-secondary"
                    style={{ flex: 1, textAlign: "center" }}
                    to={session ? roleHome(session.user.role) : "/login"}
                  >
                    Continue as {session?.user.name || "Current User"}
                  </Link>
                </div>
              </div>
            ) : session ? (
              <div>
                <p className="muted" style={{ marginBottom: "16px" }}>
                  Your account is now fully verified. You can proceed to your dashboard to manage campaigns and access all platform features.
                </p>
                <Link
                  className="btn"
                  style={{ display: "block", textAlign: "center" }}
                  to={roleHome(session.user.role)}
                >
                  Go to Dashboard
                </Link>
              </div>
            ) : (
              <div>
                <p className="muted" style={{ marginBottom: "16px" }}>
                  {verifiedEmail ? `Email ${verifiedEmail} verified.` : "Your email is verified."} Please sign in to access your account.
                </p>
                <Link
                  className="btn"
                  style={{ display: "block", textAlign: "center" }}
                  to="/login"
                >
                  Sign in
                </Link>
              </div>
            )}
          </div>
        )}

        {(status === "error" || (status === "idle" && !token)) && (
          <div role="alert">
            <p className="error" style={{ marginBottom: "16px" }}>
              ✕ {error || "Verification failed."}
            </p>
            <p className="muted" style={{ marginBottom: "16px" }}>
              The verification link may be invalid, expired, or already used. If you have an active account, sign in to resend a new verification email from your dashboard.
            </p>
            <div style={{ display: "flex", gap: "10px" }}>
              {session ? (
                <Link
                  className="btn"
                  style={{ flex: 1, textAlign: "center" }}
                  to={roleHome(session.user.role)}
                >
                  Go to Dashboard
                </Link>
              ) : (
                <Link
                  className="btn"
                  style={{ flex: 1, textAlign: "center" }}
                  to="/login"
                >
                  Sign in
                </Link>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
