import { useEffect, useState } from "react";

import { api, type Health } from "../api";

/** The public /health status in the top bar, refreshed every 10 seconds. */
export function HealthBadge() {
  const [health, setHealth] = useState<Health | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .health()
        .then((h) => {
          if (!cancelled) {
            setHealth(h);
            setFailed(false);
          }
        })
        .catch(() => {
          if (!cancelled) setFailed(true);
        });
    void load();
    const timer = window.setInterval(load, 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  if (failed) return <span className="badge bad">API unreachable</span>;
  if (!health) return <span className="badge">Checking…</span>;
  return (
    <span className={`badge ${health.status === "ok" ? "good" : "bad"}`} title={`API ${health.version}`}>
      {health.status === "ok" ? "All systems up" : "Degraded"}
    </span>
  );
}
