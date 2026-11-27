import { screen, waitFor, within } from "@testing-library/react";
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
  whole_site: true,
};
const FINISHED = { ...CRAWL, id: "c0", status: "finished", counts: { ...CRAWL.counts, queued: 0 } };
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
const now = new Date().toISOString();
const URLS = [
  { url: "http://testsite:8000/items/3", status: "fetched", depth: 1, attempts: 1, last_error: null, updated_at: now },
  { url: "http://testsite:8000/broken/1", status: "dead", depth: 1, attempts: 5, last_error: "HTTP 503", updated_at: now },
  { url: "http://testsite:8000/items/999", status: "failed", depth: 1, attempts: 1, last_error: "HTTP 404", updated_at: now },
];

type Req = { url: URL };

/** /crawls/{id}/urls answers by its status filter, like the API; totals are 10 per lane. */
function crawlUrls({ url }: Req): [number, unknown] {
  const wanted = url.searchParams.getAll("status");
  const items = URLS.filter((u) => wanted.includes(u.status));
  return [200, { items, total: 10, limit: 16, offset: 0 }];
}

/** /dlq answers by its status filter. */
function dlqBy(byStatus: Record<string, unknown[]>) {
  return ({ url }: Req): [number, unknown] => {
    const items = byStatus[url.searchParams.get("status") ?? "dead"] ?? [];
    return [200, { items, total: items.length, limit: 20, offset: 0 }];
  };
}

function dashboardAs(
  role: "admin" | "viewer",
  extra: Record<string, unknown> = {},
  crawls: unknown[] = [CRAWL],
) {
  signedIn(role);
  const api = fakeApi({
    "/auth/me": { username: role, role },
    "/health": HEALTHY,
    "/crawls": { items: crawls, total: crawls.length, limit: 10, offset: 0 },
    "/crawls/c1": {
      ...CRAWL,
      recent_errors: [
        { url: "http://testsite:8000/items/999", status: "failed", attempts: 1,
          last_error: "HTTP 404", updated_at: now },
      ],
    },
    "/crawls/c0": { ...FINISHED, recent_errors: [] },
    "/crawls/c1/urls": crawlUrls,
    "/crawls/c0/urls": crawlUrls,
    "/sites": SITES,
    "/dlq": dlqBy({ dead: [DEAD] }),
    ...extra,
  });
  renderAt("/", <AuthProvider><Dashboard /></AuthProvider>);
  return api;
}

async function startButtonReady() {
  await screen.findByRole("option", { name: "Local test site (testsite)" });
  const start = screen.getByRole("button", { name: "Start crawl" });
  await waitFor(() => expect(start).toBeEnabled());
  return start;
}

async function retryButtonFor(url: string) {
  const cell = await screen.findByText(url, { selector: "td" });
  return within(cell.closest("tr") as HTMLElement).getByRole("button", { name: "Retry" });
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

  it("puts workers at their site and the watched crawl's pages in lanes", async () => {
    dashboardAs("viewer");
    const site = await screen.findByRole("list", { name: "Workers at Local test site" });
    expect(within(site).getByTitle(/testsite:8000\/items\/9/)).toBeInTheDocument();
    expect(await screen.findByLabelText("Finished: 10")).toBeInTheDocument();
    expect(screen.getByLabelText("Dead-letter queue: 10")).toBeInTheDocument();
    expect(screen.getByLabelText("Failed: 10")).toBeInTheDocument();
    const failed = screen.getByRole("list", { name: "Failed" });
    expect(within(failed).getByText(/items\/999: HTTP 404/)).toBeInTheDocument();
  });

  it("shows a crawl's errors on demand", async () => {
    dashboardAs("viewer");
    await userEvent.click(await screen.findByRole("button", { name: "Details" }));
    expect(await screen.findByRole("cell", { name: "HTTP 404" })).toBeInTheDocument();
  });

  it("never shows a viewer the admin actions", async () => {
    const api = dashboardAs("viewer");
    await screen.findByRole("progressbar", { name: "testsite progress" });
    expect(screen.queryByRole("button", { name: "Start crawl" })).toBeNull();
    expect(screen.queryByRole("heading", { name: /Dead-letter queue/, level: 2 })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(api.calls.some((c) => c.path === "/dlq")).toBe(false); // not even fetched
  });

  it("lets an admin start a crawl", async () => {
    const api = dashboardAs(
      "admin",
      { "POST /crawls": [202, { ...CRAWL, mode: "incremental" }] },
      [FINISHED],
    );
    const start = await startButtonReady();
    await userEvent.selectOptions(screen.getByLabelText("Mode"), "incremental");
    await userEvent.click(start);
    expect(await screen.findByText("Crawl started: Local test site, incremental.")).toBeInTheDocument();
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({
      site: "testsite",
      mode: "incremental",
    });
  });

  it("does not offer to start a crawl of a site that is already being crawled", async () => {
    const api = dashboardAs("admin");
    await screen.findByRole("option", { name: "Local test site (testsite)" });
    expect(await screen.findByText(/Local test site is already being crawled/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start crawl" })).toBeDisabled();
    expect(api.calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("explains a 409 when another admin started the same crawl first", async () => {
    dashboardAs("admin", { "POST /crawls": [409, { detail: "already being crawled" }] }, [FINISHED]);
    await userEvent.click(await startButtonReady());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Local test site is already being crawled. Start another when it finishes.",
    );
  });

  it("shows the API's reason when starting a crawl fails", async () => {
    dashboardAs("admin", { "POST /crawls": [422, { detail: "unknown site 'testsite'" }] }, [FINISHED]);
    await userEvent.click(await startButtonReady());
    expect(await screen.findByRole("alert")).toHaveTextContent("unknown site 'testsite'");
  });

  it("lets an admin retry a dead-letter job and follows it", async () => {
    let retried = false;
    const api = dashboardAs("admin", {
      "POST /dlq/7/retry": () => {
        retried = true;
        return [202, { ...DEAD, status: "retried" }];
      },
      "/dlq": (req: Req) =>
        dlqBy(retried ? { retried: [{ ...DEAD, status: "retried" }] } : { dead: [DEAD] })(req),
    });
    await userEvent.click(await retryButtonFor(DEAD.url));
    expect(await screen.findByText(`Sent again: ${DEAD.url}`)).toBeInTheDocument();
    expect(await screen.findByText("running again")).toBeInTheDocument();
    expect(api.calls.filter((c) => c.method === "POST" && c.path === "/dlq/7/retry")).toHaveLength(1);
  });

  it("says when a dead-letter job was already retried by someone else", async () => {
    dashboardAs("admin", { "POST /dlq/7/retry": [409, { detail: "Not in the queue" }] });
    await userEvent.click(await retryButtonFor(DEAD.url));
    expect(await screen.findByText("That job was already sent again.")).toBeInTheDocument();
  });

  it("pauses retries while the job queue is down", async () => {
    dashboardAs("admin", { "/health": { ...HEALTHY, status: "degraded", queue: null } });
    expect(await retryButtonFor(DEAD.url)).toBeDisabled();
    expect(screen.getByText(/retries are paused/)).toBeInTheDocument();
  });

  it("does not offer to retry a job whose crawl was cancelled", async () => {
    const stranded = { ...DEAD, crawl_status: "failed", retryable: false };
    const api = dashboardAs("admin", { "/dlq": dlqBy({ dead: [stranded] }) });
    const retry = await retryButtonFor(DEAD.url);
    expect(retry).toBeDisabled();
    expect(retry).toHaveAttribute("title", "Its crawl was cancelled, so this job cannot run again");
    expect(screen.getByText("crawl cancelled")).toBeInTheDocument();
    expect(screen.getByText(/belongs to a cancelled crawl/)).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("explains a 409 when the crawl was cancelled after the page loaded", async () => {
    dashboardAs("admin", {
      "POST /dlq/7/retry": [409, { detail: "its crawl c1 was cancelled, so the job cannot run again" }],
    });
    await userEvent.click(await retryButtonFor(DEAD.url));
    expect(await screen.findByText("Its crawl was cancelled, so this job cannot run again.")).toBeInTheDocument();
  });
});
