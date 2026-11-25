import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Snippet } from "./Snippet";

function rendered(html: string) {
  const { container } = render(<Snippet html={html} />);
  return container.querySelector("p") as HTMLParagraphElement;
}

describe("Snippet", () => {
  it("highlights the matches", () => {
    const p = rendered("a <mark>whale</mark> near the <mark>harbour</mark>");
    expect([...p.querySelectorAll("mark")].map((m) => m.textContent)).toEqual(["whale", "harbour"]);
    expect(p.textContent).toBe("a whale near the harbour");
  });

  it("shows escaped markup as text", () => {
    const p = rendered("fixed &lt;script&gt;alert(1)&lt;/script&gt; for <mark>a &amp; b</mark>");
    expect(p.textContent).toBe("fixed <script>alert(1)</script> for a & b");
    expect(p.querySelector("script")).toBeNull();
  });

  it("never renders any other tag, even if the API let one through", () => {
    const p = rendered('x <img src=x onerror="window.hacked=1"><b>bold</b> <a href="javascript:1">y</a>');
    expect(p.querySelector("img, b, a")).toBeNull();
    expect(p.textContent).toBe("x bold y");
    expect((window as unknown as { hacked?: number }).hacked).toBeUndefined();
  });
});
