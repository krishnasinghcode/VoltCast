import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { errorMessage } from "../api/client";
import { useAuth } from "./AuthContext";

const DEMO_ACCOUNTS = [
  { email: "admin@cvi.local", role: "Admin" },
  { email: "manager@cvi.local", role: "Fleet manager" },
  { email: "viewer@cvi.local", role: "Viewer (read-only)" },
];

export default function LoginPage() {
  const { auth, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation() as { state?: { from?: string; expired?: boolean } };
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (auth) return <Navigate to={location.state?.from ?? "/"} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      navigate(location.state?.from ?? "/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="brand brand-lg">
          <span className="brand-mark">⚡</span>
          <div>
            <div className="brand-name">CVI Platform</div>
            <div className="muted small">Connected Vehicle Intelligence</div>
          </div>
        </div>
        {location.state?.expired && <div className="banner warn">Your session expired. Please sign in again.</div>}
        <label className="field">
          <span>Email</span>
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error && <div className="banner error">{error}</div>}
        <button className="btn primary block" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <div className="demo-accounts">
          <div className="muted small">Demo accounts (created by seed_users.py):</div>
          {DEMO_ACCOUNTS.map((a) => (
            <button type="button" key={a.email} className="link-btn" onClick={() => setEmail(a.email)}>
              {a.email} <span className="muted">· {a.role}</span>
            </button>
          ))}
        </div>
      </form>
    </div>
  );
}
