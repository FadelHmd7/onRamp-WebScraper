import { useCallback, useEffect, useRef, useState } from "react";

export interface Polled<T> {
  data: T | null;
  error: string | null;
  refresh: () => void;
}

/**
 * Load now, then every `intervalMs`. Polling pauses while the tab is hidden
 * (no point refreshing a page nobody sees) and refreshes at once when it
 * comes back. A failed poll keeps the last good data and reports the error.
 */
export function usePolling<T>(load: () => Promise<T>, intervalMs: number): Polled<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load; // always poll with the latest filters
  }, [load]);
  const alive = useRef(true);

  const refresh = useCallback(() => {
    loadRef
      .current()
      .then((value) => {
        if (!alive.current) return;
        setData(value);
        setError(null);
      })
      .catch((err: unknown) => {
        if (alive.current) setError(err instanceof Error ? err.message : String(err));
      });
  }, []);

  useEffect(() => {
    alive.current = true;
    refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, intervalMs);
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh, intervalMs]);

  return { data, error, refresh };
}
