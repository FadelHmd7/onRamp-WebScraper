import { type FormEvent, useState } from "react";
import { Navigate, useLocation } from "react-router";

import { ApiError } from "../api";
import { useAuth } from "../auth";
import { Robot } from "../components/Robot";
import { Spinner } from "../components/Spinner";

export function Login() {
  const { user, login } = useAuth();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={from} replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "Wrong username or password."
          : "Cannot reach the API. Is the stack running?",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-page">
      <div className="login-art" aria-hidden="true">
        <div className="login-crew">
          <Robot mood="done" size={56} />
          <Robot mood="working" size={72} />
          <Robot mood="idle" size={56} />
        </div>
        <p className="login-tagline">Robots that read three book sites, politely, and keep every version.</p>
      </div>
      <section className="login panel">
        <h1>Sign in</h1>
        <form onSubmit={submit}>
          <label>
            Username
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="primary" disabled={busy || !username || !password} aria-busy={busy}>
            {busy ? (
              <>
                <Spinner /> Signing in…
              </>
            ) : (
              "Sign in"
            )}
          </button>
        </form>
        <p className="muted small">Accounts come from ADMIN_* and VIEWER_* in .env.</p>
      </section>
    </main>
  );
}
