import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth";
import { fakeApi, HEALTH, renderAt, signedIn } from "../test-utils";
import { Dashboard } from "./Dashboard";

const CRAWL = {
  id: "c1",
  site: "testsite",
  mode: "full",
  status: "running",
  created_at: new Date().toISOString(),
  started_at: new Date().toISOString(),
  finished_at: null,
  seconds: 12.5,
  pages_per_minute: 420,
  total: 200,
  done: 50,
  counts: { queued: 150, fetched: 45, unchanged: 3, skipped: 0, failed: 1, dead: 1 },
  errors: 2,
};
const HEALTHY = {
  ...HEALTH,
  queue: { ready: 140, unacked: 4, backlog: 144, consumers: 4 },
  workers: {
    active: 4,
    busy: 3,
    list: [{ name: "w1", current_url: "http://testsite:8000/items/9", processed: 12 }],
  },
  running_crawls: 1,
};
const SITES = [
  { key: "testsite", name: "Local test site", base_url: "http://testsite:8000/", role: "test",
    fetch_mode: "http", requests_per_second: 100, max_pages: 700, max_depth: 40 },
];
const DEAD = {
  id: 7, crawl_id: "c1", site: "testsite", url: "http://testsite:8000/broken/1", attempts: 5,
  last_error: "HTTP 503", last_status: 503, status: "dead", failed_at: new Date().toISOString(),
};

function dashboardAs(role: "admin" | "viewer", extra: Record<string, unknown> = {}) {
  signedIn(role);
  const api = fakeApi({
    "/auth/me": { username: role, role },
    "/health": HEALTHY,
    "/crawls": { items: [CRAWL], total: 1, limit: 10, offset: 0 },
    "/crawls/c1": {
      ...CRAWL,
      recent_errors: [
        { url: "http://testsite:8000/items/999", status: "failed", attempts: 1,
          last_error: "HTTP 404", updated_at: new Date().toISOString() },
      ],
    },
    "/sites": SITES,
    "/dlq": { items: [DEAD], total: 1, limit: 20, offset: 0 },
    ...extra,
  });
  renderAt("/", <AuthProvider><Dashboard /></AuthProvider>);
  return api;
}

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("Dashboard", () => {
  it("shows queue length, active workers and crawl progress to everyone", async () => {
    dashboardAs("viewer");
    expect(await screen.findByLabelText("Queue length")).toHaveTextContent("144");
    expect(screen.getByLabelText("Active workers")).toHaveTextContent("4");
    expect(await screen.findByText("http://testsite:8000/items/9")).toBeInTheDocument();
    const bar = await screen.findByRole("progressbar", { name: "testsite progress" });
    expect(bar).toHaveAttribute("aria-valuenow", "25");
    expect(screen.getByText(/50 \/ 200 \(25%\)/)).toBeInTheDocument();
  });

  it("shows a crawl's errors on demand", async () => {
    dashboardAs("viewer");
    await userEvent.click(await screen.findByRole("button", { name: "Details" }));
    expect(await screen.findByText("HTTP 404")).toBeInTheDocument();
  });

  it("never shows a viewer the admin actions", async () => {
    const api = dashboardAs("viewer");
    await screen.findByRole("progressbar", { name: "testsite progress" });
    expect(screen.queryByRole("button", { name: "Start crawl" })).toBeNull();
    expect(screen.queryByText(/Dead-letter queue/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(api.calls.some((c) => c.path === "/dlq")).toBe(false); // not even fetched
  });

  it("lets an admin start a crawl", async () => {
    const api = dashboardAs("admin", { "POST /crawls": [202, { ...CRAWL, mode: "incremental" }] });
    const start = await screen.findByRole("button", { name: "Start crawl" });
    await screen.findByRole("option", { name: /Local test site/ });
    await userEvent.selectOptions(screen.getByLabelText("Mode"), "incremental");
    await userEvent.click(start);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Started a incremental crawl of testsite.",
    );
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({
      site: "testsite",
      mode: "incremental",
    });
  });

  it("lets an admin retry a dead-letter job", async () => {
    const api = dashboardAs("admin", { "POST /dlq/7/retry": [202, { ...DEAD, status: "retried" }] });
    const row = (await screen.findByText("http://testsite:8000/broken/1")).closest("tr");
    expect(row).not.toBeNull();
    await userEvent.click(within(row as HTMLElement).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Sent http://testsite:8000/broken/1 again.")).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "POST" && c.path === "/dlq/7/retry")).toBe(true);
  });

  it("shows the API's reason when starting a crawl fails", async () => {
    dashboardAs("admin", {
      "POST /crawls": [422, { detail: "unknown site 'testsite'" }],
    });
    await screen.findByRole("option", { name: /Local test site/ });
    await userEvent.click(screen.getByRole("button", { name: "Start crawl" }));
    expect(await screen.findByRole("status")).toHaveTextContent("unknown site 'testsite'");
  });
});
