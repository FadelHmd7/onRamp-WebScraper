import type { ButtonHTMLAttributes, ReactNode } from "react";

/** A small spinning ring, for "this is in progress". */
export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner" role={label ? "img" : undefined} aria-label={label} aria-hidden={!label} />
  );
}

/** "Loading" in place of content that has not arrived yet. */
export function Loading({ children = "Loading…" }: { children?: ReactNode }) {
  return (
    <p className="loading muted">
      <Spinner /> {children}
    </p>
  );
}

/**
 * A button that shows a spinner and its busy text while its action runs.
 * It is disabled while busy, so a second click cannot send the action twice.
 */
export function BusyButton({
  busy,
  busyText,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { busy: boolean; busyText: string }) {
  return (
    <button type="button" {...rest} disabled={busy || disabled} aria-busy={busy}>
      {busy ? (
        <>
          <Spinner /> {busyText}
        </>
      ) : (
        children
      )}
    </button>
  );
}
