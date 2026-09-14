import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { roleHome, useAuth } from "../auth/AuthContext";
import type { Role } from "../types";

export default function Signup() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", password: "", confirm: "", role: "advertiser" as "advertiser" | "publisher" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const update = (key: keyof typeof form, value: string) => setForm((current) => ({ ...current, [key]: value }));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (form.password !== form.confirm) { setError("Passwords do not match."); return; }
    setBusy(true); setError(null);
    try { const session = await register(form.name, form.email, form.password, form.role); navigate(roleHome(session.user.role as Role), { replace: true }); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not create account"); }
    finally { setBusy(false); }
  };
  return <div className="login-wrap"><div className="login-card"><h1>Create account</h1><p className="muted">Advertisers and publishers can register here.</p><form onSubmit={submit}><div className="field"><label>Name *</label><input value={form.name} onChange={(event) => update("name", event.target.value)} required /></div><div className="field"><label>Email address *</label><input type="email" value={form.email} onChange={(event) => update("email", event.target.value)} required /></div><div className="field"><label>Password *</label><input type="password" minLength={12} value={form.password} onChange={(event) => update("password", event.target.value)} required /><span className="muted">Use at least 12 characters.</span></div><div className="field"><label>Confirm password *</label><input type="password" value={form.confirm} onChange={(event) => update("confirm", event.target.value)} required /></div><div className="field"><label>Account type *</label><select value={form.role} onChange={(event) => update("role", event.target.value)}><option value="advertiser">Advertiser</option><option value="publisher">Publisher</option></select></div>{error && <p className="error">{error}</p>}<button className="btn" style={{ width: "100%" }} disabled={busy}>{busy ? "Creating account..." : "Create account"}</button></form><p className="muted">Already have an account? <Link to="/login">Sign in</Link></p></div></div>;
}
