// A fake API for UI tests: fetch is replaced by a table of handlers by path.

import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";

export type Handler = (request: { url: URL; init?: RequestInit }) => [number, unknown];

/** A handler value is a body (200), a [status, body] pair, or a function returning one. */
function isReply(value: unknown): value is [number, unknown] {
  return Array.isArray(value) && value.length === 2 && typeof value[0] === "number";
}

export interface FakeApi {
  calls: { method: string; path: string; body?: unknown }[];
}

export function fakeApi(handlers: Record<string, Handler | unknown>): FakeApi {
  const fake: FakeApi = { calls: [] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: URL | string, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const path = url.pathname.replace(/^\/api/, "");
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      fake.calls.push({ method, path, body });
      const handler = handlers[`${method} ${path}`] ?? handlers[path];
      if (handler === undefined) return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
      const [status, payload] =
        typeof handler === "function"
          ? (handler as Handler)({ url, init })
          : isReply(handler)
            ? handler
            : [200, handler];
      return new Response(JSON.stringify(payload), { status });
    }),
  );
  return fake;
}

export const HEALTH = {
  status: "ok",
  version: "0.1.0",
  checks: {},
  queue: { ready: 0, unacked: 0, backlog: 0, consumers: 2 },
  workers: { active: 2, busy: 0, list: [] },
  running_crawls: 0,
};

export function renderAt(path: string, ui: ReactElement) {
  return render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>);
}

export function signedIn(role: "admin" | "viewer") {
  sessionStorage.setItem("onramp.token", `token-${role}`);
  return { username: role === "admin" ? "ada" : "vic", role };
}
