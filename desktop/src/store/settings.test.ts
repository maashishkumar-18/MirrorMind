import { beforeEach, describe, expect, it } from "vitest";

import { useSettingsStore } from "./settings";

describe("useSettingsStore", () => {
  beforeEach(() => useSettingsStore.setState({ lastExportedAt: null }));

  it("stores and clears the last-export timestamp", () => {
    useSettingsStore.getState().setLastExportedAt("2026-09-01T00:00:00Z");
    expect(useSettingsStore.getState().lastExportedAt).toBe("2026-09-01T00:00:00Z");
    useSettingsStore.getState().setLastExportedAt(null);
    expect(useSettingsStore.getState().lastExportedAt).toBeNull();
  });
});
