import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Blocks } from "./Blocks";

describe("Blocks", () => {
  it("renders every block type as what it was", () => {
    render(
      <Blocks
        blocks={[
          { type: "heading", level: 1, text: "Pride and Prejudice" },
          { type: "paragraph", text: "It is a truth universally acknowledged." },
          { type: "list", ordered: true, items: ["one", "two"] },
          { type: "table", caption: "Product information", header: [], row_headers: true,
            rows: [["UPC", "a897"], ["Tax", "£0.00"]] },
          { type: "code", text: "x = 1" },
          { type: "quote", text: "A quote." },
        ]}
      />,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Pride and Prejudice" })).toBeInTheDocument();
    expect(screen.getByText("It is a truth universally acknowledged.").tagName).toBe("P");
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(2);
    const table = screen.getByRole("table", { name: "Product information" });
    expect(within(table).getByRole("rowheader", { name: "UPC" })).toBeInTheDocument();
    expect(within(table).getByRole("cell", { name: "a897" })).toBeInTheDocument();
    expect(screen.getByText("x = 1").closest("pre")).not.toBeNull();
    expect(screen.getByText("A quote.").tagName).toBe("BLOCKQUOTE");
  });

  it("shows text that looks like markup as text", () => {
    render(<Blocks blocks={[{ type: "paragraph", text: "<script>alert(1)</script>" }]} />);
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
  });
});
