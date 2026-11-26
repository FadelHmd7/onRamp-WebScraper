import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HealthBadge } from "./HealthBadge";

function respond(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status })),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("HealthBadge", () => {
  it("shows all systems up", async () => {
    respond(200, { status: "ok", version: "0.1.0" });
    render(<HealthBadge />);
    expect(await screen.findByText("All systems up")).toBeInTheDocument();
  });

  it("reads a 503 as degraded, not as unreachable", async () => {
    respond(503, { status: "degraded", version: "0.1.0" });
    render(<HealthBadge />);
    expect(await screen.findByText("Degraded")).toBeInTheDocument();
  });

  it("says when the API cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("network"))));
    render(<HealthBadge />);
    expect(await screen.findByText("API unreachable")).toBeInTheDocument();
  });
});
