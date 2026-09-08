import { describe, expect, it } from "vitest";

import { formatBytes, formatEta, formatSpeed, guardDecision, rowAction } from "./modelRow";

describe("rowAction", () => {
  it.each([
    ["not_installed", "download"],
    [undefined, "download"],
    ["downloading", "downloading"],
    ["available", "activate"],
    ["active", "active"],
  ] as const)("%s → %s", (status, expected) => {
    expect(rowAction(status)).toBe(expected);
  });
});

describe("guardDecision", () => {
  it("starting → loading", () => {
    expect(guardDecision("starting", true)).toBe("loading");
    expect(guardDecision("starting", false)).toBe("loading");
  });
  it("degraded / exited → pass (fe.5 banner covers it; model.* fail anyway)", () => {
    expect(guardDecision("degraded", true)).toBe("pass");
    expect(guardDecision("exited", true)).toBe("pass");
  });
  it("ready → first-run only when setup is required", () => {
    expect(guardDecision("ready", true)).toBe("first-run");
    expect(guardDecision("ready", false)).toBe("pass");
  });
});

describe("formatters", () => {
  it("formatBytes", () => {
    expect(formatBytes(4_920_000_000)).toBe("4.9 GB");
    expect(formatBytes(90_000_000)).toBe("90 MB");
  });
  it("formatSpeed", () => {
    expect(formatSpeed(12.34)).toBe("12.3 MB/s");
  });
  it("formatEta", () => {
    expect(formatEta(null)).toBe("—");
    expect(formatEta(-1)).toBe("—");
    expect(formatEta(45)).toBe("~45s");
    expect(formatEta(130)).toBe("~2m 10s");
    expect(formatEta(120)).toBe("~2m");
    expect(formatEta(3720)).toBe("~1h 2m");
  });
});
