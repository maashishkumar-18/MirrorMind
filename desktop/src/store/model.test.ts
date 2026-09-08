import { beforeEach, describe, expect, it } from "vitest";

import { useModelStore } from "./model";

const initial = useModelStore.getState();
beforeEach(() => useModelStore.setState(initial, true));

describe("useModelStore", () => {
  it("hydrates the active model + setup gate from app.ready", () => {
    useModelStore
      .getState()
      .hydrateFromReady({ ipc_version: 1, model_setup_required: false, active_model: "llama3.1:8b" });
    const s = useModelStore.getState();
    expect(s.activeModel).toBe("llama3.1:8b");
    expect(s.modelSetupRequired).toBe(false);
  });

  it("tracks and clears download progress", () => {
    useModelStore.getState().setDownloadProgress({
      name: "mistral:latest",
      phase: "downloading",
      percent: 42,
      speed_mbps: 10,
      eta_seconds: 30,
      message: null,
    });
    expect(useModelStore.getState().downloadProgress?.percent).toBe(42);
    useModelStore.getState().clearDownloadProgress();
    expect(useModelStore.getState().downloadProgress).toBeNull();
  });
});
