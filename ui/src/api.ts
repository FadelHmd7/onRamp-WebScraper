// The API client: every request goes through `request`, which adds the token
// and turns errors into ApiError. Types mirror the API's response models
// (see http://localhost:9060/docs).

export type Role = "admin" | "viewer";

export interface Me {
  username: string;
  role: Role;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type UrlStatus = "queued" | "fetched" | "unchanged" | "skipped" | "failed" | "dead";

export interface Crawl {
  id: string;
  site: string;
  mode: "full" | "incremental";
  status: "queued" | "running" | "finished" | "failed";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  seconds: number | null;
  pages_per_minute: number | null;
  total: number;
  done: number;
  counts: Record<UrlStatus, number>;
  errors: number;
}

export interface CrawlError {
  url: string;
  status: string;
  attempts: number;
  last_error: string | null;
  updated_at: string;
}

export interface CrawlDetail extends Crawl {
  recent_errors: CrawlError[];
}

export interface Check {
  ok: boolean;
  ms: number;
  error: string | null;
}

export interface Health {
  status: "ok" | "degraded";
  version: string;
  checks: Record<string, Check>;
  queue: { ready: number; unacked: number; backlog: number; consumers: number } | null;
  workers: {
    active: number;
    busy: number;
    list: { name: string; current_url: string | null; processed: number }[];
  } | null;
  running_crawls: number | null;
}

export interface DeadLetter {
  id: number;
  crawl_id: string;
  site: string;
  url: string;
  attempts: number;
  last_error: string;
  last_status: number | null;
  status: "dead" | "retried" | "resolved";
  failed_at: string;
}

export interface Site {
  key: string;
  name: string;
  base_url: string;
  role: string;
  fetch_mode: "http" | "browser";
  requests_per_second: number;
  max_pages: number;
  max_depth: number;
}

export interface SearchHit {
  id: number;
  url: string;
  site: string;
  title: string;
  version: number;
  crawled_at: string;
  rank: number;
  snippet: string; // escaped text with <mark> around matches
}

export interface DocumentSummary {
  id: number;
  url: string;
  site: string;
  title: string;
  status: "active" | "removed";
  version: number;
  content_hash: string;
  crawled_at: string;
  last_seen_at: string;
  removed_at: string | null;
}

export type Block =
  | { type: "heading"; level: number; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | {
      type: "table";
      caption: string | null;
      header: string[];
      rows: string[][];
      row_headers: boolean;
    }
  | { type: "code"; text: string }
  | { type: "quote"; text: string };

export interface DocumentDetail extends DocumentSummary {
  first_seen_at: string;
  versions: number;
  blocks: Block[];
  fields: Record<string, unknown>;
  links: { url: string; text: string; nofollow: boolean }[];
  raw: string;
}

export interface VersionSummary {
  version: number;
  crawled_at: string;
  content_hash: string;
  title: string;
  crawl_id: string | null;
  current: boolean;
}

export interface Version extends VersionSummary {
  blocks: Block[];
  fields: Record<string, unknown>;
}

export interface VersionHistory {
  id: number;
  url: string;
  current_version: number;
  status: "active" | "removed";
  versions: VersionSummary[];
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const BASE = "/api";
const TOKEN_KEY = "onramp.token";

// The token lives in sessionStorage: it survives a reload but not closing the
// tab. Every read and write is guarded, so private browsing still works.
export const tokenStore = {
  get(): string | null {
    try {
      return sessionStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set(token: string | null): void {
    try {
      if (token) sessionStorage.setItem(TOKEN_KEY, token);
      else sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      // storage unavailable: the session lasts until reload
    }
  },
};

let onUnauthorized: () => void = () => {};

/** Called when the API answers 401 to a logged-in request (expired or revoked token). */
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

async function detail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const value = (body as { detail: unknown }).detail;
      return typeof value === "string" ? value : JSON.stringify(value);
    }
  } catch {
    // not JSON
  }
  return `${response.status} ${response.statusText}`;
}

type Query = Record<string, string | number | undefined | null>;

export async function request<T>(
  path: string,
  options: {
    method?: string;
    query?: Query;
    body?: unknown;
    form?: URLSearchParams;
    /** Error statuses whose body is still the answer (503 from /health). */
    accept?: number[];
  } = {},
): Promise<T> {
  const url = new URL(BASE + path, window.location.origin);
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  const response = await fetch(url, { method: options.method ?? "GET", headers, body });
  if (response.status === 401 && token) onUnauthorized();
  if (!response.ok && !options.accept?.includes(response.status)) {
    throw new ApiError(response.status, await detail(response));
  }
  return (await response.json()) as T;
}

export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; role: Role }>("/auth/token", {
      method: "POST",
      form: new URLSearchParams({ username, password }),
    }),
  me: () => request<Me>("/auth/me"),
  // 503 means "degraded": the body still says what is down.
  health: () => request<Health>("/health", { accept: [503] }),
  sites: () => request<Site[]>("/sites"),
  crawls: (query: Query = {}) => request<Page<Crawl>>("/crawls", { query }),
  crawl: (id: string) => request<CrawlDetail>(`/crawls/${id}`),
  startCrawl: (body: { site: string; mode: "full" | "incremental" }) =>
    request<Crawl>("/crawls", { method: "POST", body }),
  deadLetters: (query: Query = {}) => request<Page<DeadLetter>>("/dlq", { query }),
  retryDeadLetter: (id: number) => request<DeadLetter>(`/dlq/${id}/retry`, { method: "POST" }),
  search: (query: Query) => request<Page<SearchHit> & { query: string }>("/search", { query }),
  documents: (query: Query = {}) => request<Page<DocumentSummary>>("/documents", { query }),
  document: (id: number) => request<DocumentDetail>(`/documents/${id}`),
  versions: (id: number) => request<VersionHistory>(`/documents/${id}/versions`),
  version: (id: number, version: number) =>
    request<Version>(`/documents/${id}/versions/${version}`),
};
