import { NavLink, Route, Routes } from "react-router";

import { AuthProvider, RequireAuth, useAuth } from "./auth";
import { HealthBadge } from "./components/HealthBadge";
import { Dashboard } from "./pages/Dashboard";
import { Login } from "./pages/Login";

export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="*"
          element={
            <RequireAuth>
              <Shell />
            </RequireAuth>
          }
        />
      </Routes>
    </AuthProvider>
  );
}

function Shell() {
  const { user, logout } = useAuth();
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">onRamp Scraper</span>
        <nav aria-label="Main">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
        </nav>
        <HealthBadge />
        <span className="user">
          {user?.username}
          <span className={`badge ${user?.role === "admin" ? "warn" : ""}`}>{user?.role}</span>
          <button type="button" className="secondary" onClick={logout}>
            Sign out
          </button>
        </span>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
}

function NotFound() {
  return (
    <section>
      <h1>Not found</h1>
      <p className="muted">There is no page at this address.</p>
    </section>
  );
}
