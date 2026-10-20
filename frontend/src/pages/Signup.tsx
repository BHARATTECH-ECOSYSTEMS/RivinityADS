import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export default function Signup() {
  const { register } = useAuth();
  const [form, setForm] = useState({ name: "", email: "", password: "", confirm: "", role: "advertiser" as "advertiser" | "publisher" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [registeredEmail, setRegisteredEmail] = useState<string | null>(null);
  const update = (key: keyof typeof form, value: string) => setForm((current) => ({ ...current, [key]: value }));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (form.password !== form.confirm) { setError("Passwords do not match."); return; }
    setBusy(true); setError(null);
    try { await register(form.name, form.email, form.password, form.role); setRegisteredEmail(form.email); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not create account"); }
    finally { setBusy(false); }
  };
  return (
    <div className="login-wrap">
      <div className="login-card">
        <h1>Create account</h1>
        {registeredEmail ? (
          <>
            <p role="status" className="success">
              Account created for <strong>{registeredEmail}</strong>.
            </p>
            <p className="muted" style={{ marginBottom: "16px" }}>
              We've sent a verification email to your address. Please check your inbox and verify your email to unlock all features. You can sign in now to access your account dashboard.
            </p>
            <Link className="btn" style={{ display: "block", textAlign: "center" }} to="/login">
              Sign in
            </Link>
          </>
        ) : (
          <>
            <p className="muted">Advertisers and publishers can register here.</p>
            <form onSubmit={submit}>
              <div className="field">
                <label htmlFor="signup-name">Name *</label>
                <input
                  id="signup-name"
                  value={form.name}
                  onChange={(event) => update("name", event.target.value)}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="signup-email">Email address *</label>
                <input
                  id="signup-email"
                  type="email"
                  autoComplete="email"
                  value={form.email}
                  onChange={(event) => update("email", event.target.value)}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="signup-password">Password *</label>
                <input
                  id="signup-password"
                  type="password"
                  autoComplete="new-password"
                  minLength={12}
                  value={form.password}
                  onChange={(event) => update("password", event.target.value)}
                  required
                />
                <span className="muted">Use at least 12 characters.</span>
              </div>
              <div className="field">
                <label htmlFor="signup-confirm">Confirm password *</label>
                <input
                  id="signup-confirm"
                  type="password"
                  autoComplete="new-password"
                  value={form.confirm}
                  onChange={(event) => update("confirm", event.target.value)}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="signup-role">Account type *</label>
                <select
                  id="signup-role"
                  value={form.role}
                  onChange={(event) => update("role", event.target.value)}
                >
                  <option value="advertiser">Advertiser</option>
                  <option value="publisher">Publisher</option>
                </select>
              </div>
              {error && <p role="alert" className="error">{error}</p>}
              <button className="btn" style={{ width: "100%" }} disabled={busy}>
                {busy ? "Creating account..." : "Create account"}
              </button>
            </form>
            <p className="muted">
              Already have an account? <Link to="/login">Sign in</Link>
            </p>
          </>
        )}
      </div>
    </div>
  );
}
