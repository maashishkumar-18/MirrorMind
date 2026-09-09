import { describe, expect, it } from "vitest";

import {
  activeTab,
  isValidIdle,
  isValidTime,
  needsExportBadge,
  SETTINGS_TABS,
} from "./settingsView";

describe("activeTab", () => {
  it("passes through a known tab", () => {
    expect(activeTab("models")).toBe("models");
    expect(activeTab("diagnostics")).toBe("diagnostics");
  });
  it("falls back to general for unknown / missing", () => {
    expect(activeTab(null)).toBe("general");
    expect(activeTab(undefined)).toBe("general");
    expect(activeTab("nonsense")).toBe("general");
    expect(activeTab("")).toBe("general");
  });
  it("has five tabs", () => {
    expect(SETTINGS_TABS).toHaveLength(5);
  });
});

describe("needsExportBadge", () => {
  const now = "2026-09-10T00:00:00Z";
  it("flags a never-exported state", () => {
    expect(needsExportBadge(null, now)).toBe(true);
  });
  it("flags an export older than 30 days", () => {
    expect(needsExportBadge("2026-08-01T00:00:00Z", now)).toBe(true);
  });
  it("does not flag a recent export", () => {
    expect(needsExportBadge("2026-09-01T00:00:00Z", now)).toBe(false);
  });
  it("flags an unparseable timestamp defensively", () => {
    expect(needsExportBadge("not-a-date", now)).toBe(true);
  });
});

describe("form validators", () => {
  it("idle bounds", () => {
    expect(isValidIdle(1)).toBe(true);
    expect(isValidIdle(1440)).toBe(true);
    expect(isValidIdle(0)).toBe(false);
    expect(isValidIdle(1441)).toBe(false);
    expect(isValidIdle(45.5)).toBe(false);
    expect(isValidIdle(NaN)).toBe(false);
  });
  it("time format", () => {
    expect(isValidTime("00:00")).toBe(true);
    expect(isValidTime("23:59")).toBe(true);
    expect(isValidTime("21:00")).toBe(true);
    expect(isValidTime("24:00")).toBe(false);
    expect(isValidTime("9:00")).toBe(false);
    expect(isValidTime("21:60")).toBe(false);
    expect(isValidTime("")).toBe(false);
  });
});
