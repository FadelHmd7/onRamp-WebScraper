// Short confirmations ("Crawl started") and failures, in one live region in
// the corner. Each disappears after a few seconds, or when it is dismissed.

import { createContext, type ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";

export type Tone = "good" | "bad" | "info";

interface Toast {
  id: number;
  tone: Tone;
  text: string;
}

type Notify = (tone: Tone, text: string) => void;

const ToastContext = createContext<Notify | null>(null);

const SHOW_MS = 6000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const notify = useCallback<Notify>(
    (tone, text) => {
      const id = next.current++;
      setToasts((list) => [...list.slice(-3), { id, tone, text }]);
      window.setTimeout(() => dismiss(id), SHOW_MS);
    },
    [dismiss],
  );

  const value = useMemo(() => notify, [notify]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone}`}>
            <span>{t.text}</span>
            <button type="button" className="ghost" onClick={() => dismiss(t.id)} aria-label="Dismiss">
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components -- the hook belongs with its provider
export function useToast(): Notify {
  const notify = useContext(ToastContext);
  if (!notify) throw new Error("useToast must be used inside <ToastProvider>");
  return notify;
}
