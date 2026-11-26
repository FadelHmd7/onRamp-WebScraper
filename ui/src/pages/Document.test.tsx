import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fakeApi, renderAt } from "../test-utils";
import { DocumentPage } from "./Document";

const NOW = new Date().toISOString();
const paragraph = (text: string) => ({ type: "paragraph", text });

function version(n: number, price: string) {
  return {
    version: n,
    crawled_at: NOW,
    content_hash: `${n}`.repeat(64),
    title: "Pricing",
    crawl_id: `crawl-${n}`,
    current: n === 3,
    blocks: [{ type: "heading", level: 1, text: "Pricing" }, paragraph(`The plan costs ${price}.`)],
    fields: { price: Number(price.slice(1)) },
  };
}

const VERSIONS = [version(1, "$10"), version(2, "$12"), version(3, "$15")];

const DOC = {
  id: 5,
  url: "http://testsite:8000/editable/pricing",
  site: "testsite",
  title: "Pricing",
  status: "active",
  version: 3,
  content_hash: "3".repeat(64),
  crawled_at: NOW,
  last_seen_at: NOW,
  first_seen_at: NOW,
  removed_at: null,
  versions: 3,
  blocks: VERSIONS[2]!.blocks,
  fields: { price: 15, authors: ["Ada", "Vic"], info: { UPC: "a897" } },
  links: [{ url: "http://testsite:8000/catalog?page=1", text: "Catalog", nofollow: false }],
  raw: "/pages/5/raw",
};

function api(overrides: Record<string, unknown> = {}) {
  return fakeApi({
    "/documents/5": DOC,
    "/documents/5/versions": {
      id: 5,
      url: DOC.url,
      current_version: 3,
      status: "active",
      versions: [...VERSIONS].reverse().map(({ blocks, fields, ...summary }) => summary),
    },
    "/documents/5/versions/1": VERSIONS[0],
    "/documents/5/versions/2": VERSIONS[1],
    "/pages/5/raw": {
      page_id: 5, url: DOC.url, site: "testsite", status: "active",
      fetch: { id: 1, crawl_id: "c", fetched_at: NOW, status_code: 200, outcome: "ok" },
      elapsed_ms: 12, headers: {}, error: null,
      html: "<html><script>alert('raw')</script><p>The plan costs $15.</p></html>",
      history: [{ id: 1, crawl_id: "c", fetched_at: NOW, status_code: 200, outcome: "ok" }],
    },
    ...overrides,
  });
}

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.search}</output>;
}

function renderDoc(path = "/documents/5") {
  return renderAt(
    path,
    <Routes>
      <Route path="/documents/:id" element={<><DocumentPage /><Where /></>} />
    </Routes>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Document page", () => {
  it("shows the current content, source URL and fields", async () => {
    api();
    renderDoc();
    expect(await screen.findByRole("heading", { level: 1, name: "Pricing" })).toBeInTheDocument();
    expect(screen.getByText("The plan costs $15.")).toBeInTheDocument();
    const source = screen.getByRole("link", { name: DOC.url });
    expect(source).toHaveAttribute("href", DOC.url);
    expect(source).toHaveAttribute("rel", "noopener noreferrer nofollow");
    expect(screen.getByRole("rowheader", { name: "authors" }).nextSibling).toHaveTextContent("Ada, Vic");
    expect(screen.getByRole("rowheader", { name: "info" }).nextSibling).toHaveTextContent("UPC: a897");
    expect(screen.getByText(/version 3 of 3/)).toBeInTheDocument();
  });

  it("lists every version and shows an old one with what changed", async () => {
    api();
    renderDoc();
    const buttons = await screen.findAllByRole("button", { name: /^Version \d/ });
    expect(buttons.map((b) => b.textContent)).toEqual([
      "Version 3 (current)",
      "Version 2",
      "Version 1",
    ]);
    expect(buttons[0]).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("button", { name: "Version 2" }));
    expect(await screen.findByText(/Viewing/)).toHaveTextContent("Viewing version 2");
    expect(screen.getByTestId("where")).toHaveTextContent("?v=2");
    expect(screen.getByRole("heading", { name: "Changed since version 1" })).toBeInTheDocument();
    expect(screen.getByText("The plan costs $10.").closest("li")).toHaveClass("removed");
    expect(screen.getAllByText("The plan costs $12.")[0]!.closest("li")).toHaveClass("added");

    await userEvent.click(screen.getByRole("button", { name: "Version 3 (current)" }));
    expect(screen.queryByText(/Viewing/)).toBeNull();
    expect(screen.getByText("The plan costs $15.")).toBeInTheDocument();
  });

  it("opens a version from the URL; the first version has nothing to compare with", async () => {
    api();
    renderDoc("/documents/5?v=1");
    expect(await screen.findByText(/Viewing/)).toHaveTextContent("Viewing version 1");
    expect(screen.queryByRole("heading", { name: /Changed since/ })).toBeNull();
    expect(screen.getByText("The plan costs $10.")).toBeInTheDocument();
  });

  it("marks a page that was removed from the site", async () => {
    api({ "/documents/5": { ...DOC, status: "removed", removed_at: NOW } });
    renderDoc();
    expect(await screen.findByText(/removed from the site/)).toBeInTheDocument();
  });

  it("shows the raw HTML as text, never as a page", async () => {
    api();
    renderDoc();
    await userEvent.click(await screen.findByRole("button", { name: "Show the HTML as fetched" }));
    const pre = await screen.findByText(/alert\('raw'\)/);
    expect(pre.tagName).toBe("PRE");
    expect(pre.querySelector("script")).toBeNull();
    expect(screen.getByText(/HTTP 200 · ok/)).toBeInTheDocument();
  });

  it("says when the document does not exist", async () => {
    api({ "/documents/5": [404, { detail: "No such document" }] });
    renderDoc();
    expect(await screen.findByRole("alert")).toHaveTextContent("No such document");
  });
});
