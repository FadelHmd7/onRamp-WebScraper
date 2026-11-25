import { type FormEvent, useState } from "react";
import { Navigate, useLocation } from "react-router";

import { ApiError } from "../api";
import { useAuth } from "../auth";

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
        <button type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p className="muted">Accounts come from ADMIN_* and VIEWER_* in .env.</p>
    </section>
  );
}
