import { describe, expect, it } from "vitest";

import { barWidths, formatMs, formatPct, levelParam, reportMailto } from "./diagnosticsView";

describe("levelParam", () => {
  it("maps ALL to null and everything else through", () => {
    expect(levelParam("ALL")).toBeNull();
    expect(levelParam("WARNING")).toBe("WARNING");
  });
});

describe("barWidths", () => {
  it("splits a sample into percentages", () => {
    expect(barWidths({ high: 1, medium: 1, low: 1, none: 1 })).toEqual({
      high: 25,
      medium: 25,
      low: 25,
      none: 25,
    });
  });
  it("is all-zero for an empty sample", () => {
    expect(barWidths({ high: 0, medium: 0, low: 0, none: 0 })).toEqual({
      high: 0,
      medium: 0,
      low: 0,
      none: 0,
    });
  });
});

describe("formatters", () => {
  it("renders 'not tracked yet' for null", () => {
    expect(formatPct(null)).toBe("not tracked yet");
    expect(formatMs(undefined)).toBe("not tracked yet");
  });
  it("renders values", () => {
    expect(formatPct(0.667)).toBe("67%");
    expect(formatMs(41.2)).toBe("41 ms");
  });
});

describe("reportMailto", () => {
  it("builds a mailto that names the saved file", () => {
    const url = reportMailto("C:/x/report.txt");
    expect(url.startsWith("mailto:?subject=")).toBe(true);
    expect(decodeURIComponent(url)).toContain("C:/x/report.txt");
  });
});
