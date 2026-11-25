import { NavLink, Route, Routes } from "react-router";

import { HealthBadge } from "./components/HealthBadge";

export function App() {
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
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
}

function Home() {
  return (
    <section>
      <h1>Dashboard</h1>
      <p className="muted">The UI is served by nginx and reaches the API through /api.</p>
    </section>
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
