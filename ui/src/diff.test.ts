import { describe, expect, it } from "vitest";

import type { Block } from "./api";
import { blockText, diffBlocks } from "./diff";

const p = (text: string): Block => ({ type: "paragraph", text });

describe("diffBlocks", () => {
  it("finds nothing between equal versions", () => {
    expect(diffBlocks([p("a"), p("b")], [p("a"), p("b")])).toEqual([]);
  });

  it("shows an edited paragraph as removed then added, in page order", () => {
    expect(diffBlocks([p("title"), p("costs $10"), p("end")], [p("title"), p("costs $12"), p("end")])).toEqual([
      { kind: "removed", text: "costs $10" },
      { kind: "added", text: "costs $12" },
    ]);
  });

  it("finds insertions and deletions anywhere", () => {
    expect(diffBlocks([p("a"), p("b"), p("c")], [p("x"), p("a"), p("c")])).toEqual([
      { kind: "added", text: "x" },
      { kind: "removed", text: "b" },
    ]);
    expect(diffBlocks([], [p("new")])).toEqual([{ kind: "added", text: "new" }]);
    expect(diffBlocks([p("old")], [])).toEqual([{ kind: "removed", text: "old" }]);
  });

  it("compares tables and lists by their text", () => {
    const table = (price: string): Block => ({
      type: "table", caption: null, header: [], rows: [["Price", price]], row_headers: true,
    });
    expect(diffBlocks([table("£1")], [table("£2")])).toEqual([
      { kind: "removed", text: "Price | £1" },
      { kind: "added", text: "Price | £2" },
    ]);
    expect(blockText({ type: "list", ordered: false, items: ["a", "b"] })).toBe("a\nb");
  });
});
