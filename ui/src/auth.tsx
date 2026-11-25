// Who is logged in, and what they may see.
//
// The API decides what each role may do (it answers 403 otherwise). The UI
// only avoids showing a viewer buttons that would fail: <AdminOnly> renders
// nothing for a viewer, so admin actions are absent from the page, not hidden.

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { Navigate, useLocation } from "react-router";

import { api, type Me, setUnauthorizedHandler, tokenStore } from "./api";

interface AuthState {
  user: Me | null;
  loading: boolean; // checking a token saved from an earlier visit
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(() => tokenStore.get() !== null);

  const logout = useCallback(() => {
    tokenStore.set(null);
    setUser(null);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(logout); // an expired or revoked token ends the session
    if (!tokenStore.get()) return;
    let cancelled = false;
    api
      .me()
      .then((me) => {
        if (!cancelled) setUser(me);
      })
      .catch(() => {
        if (!cancelled) logout();
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [logout]);

  const login = useCallback(async (username: string, password: string) => {
    const { access_token } = await api.login(username, password);
    tokenStore.set(access_token);
    setUser(await api.me());
  }, []);

  const value = useMemo(() => ({ user, loading, login, logout }), [user, loading, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components -- the hook belongs with its provider
export function useAuth(): AuthState {
  const state = useContext(AuthContext);
  if (!state) throw new Error("useAuth must be used inside <AuthProvider>");
  return state;
}

/** Pages that need a login; anyone else goes to /login and comes back after. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <p className="muted">Loading…</p>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return children;
}

/** Rendered only for admins. A viewer gets nothing: no disabled button, no hint. */
export function AdminOnly({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return user?.role === "admin" ? children : null;
}
