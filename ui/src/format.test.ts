import { describe, expect, it } from "vitest";

import { ago, duration, percent } from "./format";

describe("format", () => {
  const now = new Date("2026-11-25T12:00:00Z");
  it.each([
    ["2026-11-25T11:59:50Z", "just now"],
    ["2026-11-25T11:57:00Z", "3 min ago"],
    ["2026-11-25T09:00:00Z", "3 h ago"],
  ])("ago(%s) = %s", (iso, text) => expect(ago(iso, now)).toBe(text));

  it.each([
    [null, "–"],
    [42.4, "42 s"],
    [95.4, "1 min 35 s"],
    [3725, "1 h 2 min"],
  ])("duration(%s) = %s", (seconds, text) => expect(duration(seconds)).toBe(text));

  it("percent never divides by zero", () => {
    expect(percent(0, 0)).toBe(0);
    expect(percent(50, 200)).toBe(25);
  });
});
