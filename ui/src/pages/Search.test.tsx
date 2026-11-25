import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fakeApi, renderAt } from "../test-utils";
import { Search } from "./Search";

const SITES = [
  { key: "books-toscrape", name: "Books to Scrape", base_url: "", role: "static",
    fetch_mode: "http", requests_per_second: 2, max_pages: 300, max_depth: 60 },
  { key: "standard-ebooks", name: "Standard Ebooks", base_url: "", role: "paginated",
    fetch_mode: "http", requests_per_second: 1, max_pages: 700, max_depth: 140 },
];

function hit(id: number) {
  return {
    id, url: `https://standardebooks.org/ebooks/${id}`, site: "standard-ebooks",
    title: `Book ${id}`, version: 1, crawled_at: new Date().toISOString(), rank: 0.5,
    snippet: `about a <mark>whale</mark> #${id}`,
  };
}

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname + location.search}</output>;
}

function renderSearch(path: string) {
  return renderAt(
    path,
    <Routes>
      <Route path="/search" element={<><Search /><Where /></>} />
      <Route path="/documents/:id" element={<Where />} />
    </Routes>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Search", () => {
  it("searches what the URL says and links each result to its document", async () => {
    const api = fakeApi({
      "/sites": SITES,
      "/search": { query: "whale", items: [hit(1), hit(2)], total: 2, limit: 20, offset: 0 },
    });
    renderSearch("/search?q=whale&site=standard-ebooks");
    expect(await screen.findByText("2 results for “whale”")).toBeInTheDocument();
    const request = api.calls.find((c) => c.path === "/search");
    expect(request).toBeDefined();
    const marks = document.querySelectorAll("mark");
    expect(marks).toHaveLength(2);
    await userEvent.click(screen.getByRole("link", { name: "Book 2" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/documents/2");
  });

  it("puts the query and site in the URL", async () => {
    const seen: string[] = [];
    fakeApi({
      "/sites": SITES,
      "/documents": { items: [], total: 0, limit: 20, offset: 0 },
      "/search": ({ url }: { url: URL }) => {
        seen.push(url.search);
        return [200, { query: "x", items: [], total: 0, limit: 20, offset: 0 }];
      },
    });
    renderSearch("/search");
    await screen.findByRole("option", { name: "Books to Scrape" });
    await userEvent.selectOptions(screen.getByLabelText("Site"), "books-toscrape");
    await userEvent.type(screen.getByLabelText("Search words"), "pride prejudice{enter}");
    expect(await screen.findByTestId("where")).toHaveTextContent(
      "/search?site=books-toscrape&q=pride+prejudice",
    );
    await screen.findByText("0 results for “pride prejudice”");
    expect(seen.at(-1)).toContain("q=pride+prejudice");
    expect(seen.at(-1)).toContain("site=books-toscrape");
  });

  it("pages through results", async () => {
    const offsets: string[] = [];
    fakeApi({
      "/sites": SITES,
      "/search": ({ url }: { url: URL }) => {
        offsets.push(url.searchParams.get("offset") ?? "");
        return [200, { query: "whale", items: [hit(1)], total: 45, limit: 20, offset: 0 }];
      },
    });
    renderSearch("/search?q=whale");
    expect(await screen.findByText("Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2 of 3")).toBeInTheDocument();
    expect(offsets).toEqual(["0", "20"]);
  });

  it("without a query, browses the newest documents", async () => {
    fakeApi({
      "/sites": SITES,
      "/documents": {
        items: [{ id: 9, url: "https://x/9", site: "books-toscrape", title: "Sapiens", status: "active",
          version: 2, content_hash: "h", crawled_at: new Date().toISOString(),
          last_seen_at: new Date().toISOString(), removed_at: null }],
        total: 1, limit: 20, offset: 0,
      },
    });
    renderSearch("/search");
    expect(await screen.findByText("1 documents, most recently changed first")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sapiens" })).toHaveAttribute("href", "/documents/9");
  });

  it("shows the API's error", async () => {
    fakeApi({ "/sites": SITES, "/search": [422, { detail: "String should have at most 200 characters" }] });
    renderSearch("/search?q=too-long");
    expect(await screen.findByRole("alert")).toHaveTextContent("at most 200 characters");
  });
});
