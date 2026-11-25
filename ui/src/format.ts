/** "3 min ago", "2 h ago", or a date for anything older than a day. */
export function ago(iso: string, now: Date = new Date()): string {
  const seconds = Math.round((now.getTime() - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

/** 95.4 -> "1 min 35 s". */
export function duration(seconds: number | null): string {
  if (seconds === null) return "–";
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min ${s % 60} s` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function percent(done: number, total: number): number {
  return total > 0 ? Math.round((done / total) * 100) : 0;
}
