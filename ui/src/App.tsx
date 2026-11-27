import { NavLink, Route, Routes } from "react-router";

import { AuthProvider, RequireAuth, useAuth } from "./auth";
import { HealthBadge } from "./components/HealthBadge";
import { Robot } from "./components/Robot";
import { Dashboard } from "./pages/Dashboard";
import { DocumentPage } from "./pages/Document";
import { Login } from "./pages/Login";
import { Search } from "./pages/Search";
import { ToastProvider } from "./toast";

export function App() {
  return (
    <AuthProvider>
      <ToastProvider>
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
      </ToastProvider>
    </AuthProvider>
  );
}

function Shell() {
  const { user, logout } = useAuth();
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">
          <Robot mood="idle" size={22} />
          onRamp Scraper
        </span>
        <nav aria-label="Main">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/search">Search</NavLink>
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
          <Route path="/search" element={<Search />} />
          <Route path="/documents/:id" element={<DocumentPage />} />
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
