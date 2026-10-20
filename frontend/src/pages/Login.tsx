import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { roleHome, useAuth } from "../auth/AuthContext";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null);
    try { const session = await login(email, password); const from = (location.state as { from?: string } | null)?.from; navigate(from && from !== "/login" ? from : roleHome(session.user.role), { replace: true }); }
    catch (err) { setError(err instanceof Error ? err.message : "Login failed"); }
    finally { setBusy(false); }
  };
  return (
    <div className="login-wrap">
      <div className="login-card">
        <h1>Sign in</h1>
        <p className="muted">Access your advertiser or publisher account.</p>
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="login-email">Email address *</label>
            <input
              id="login-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>
          <div className="field">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
              <label htmlFor="login-password" style={{ margin: 0 }}>Password *</label>
              <Link to="/forgot-password" style={{ fontSize: "12px", color: "#2563eb" }}>
                Forgot password?
              </Link>
            </div>
            <input
              id="login-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </div>
          {error && <p role="alert" className="error">{error}</p>}
          <button className="btn" style={{ width: "100%" }} disabled={busy}>
            {busy ? "Signing in..." : "Sign in"}
          </button>
        </form>
        <p className="muted">New here? <Link to="/signup">Create an account</Link></p>
      </div>
    </div>
  );
}
