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

  it("setCatalog / setCatalogError", () => {
    useModelStore.getState().setCatalogError("nope");
    expect(useModelStore.getState().catalogError).toBe("nope");
    useModelStore.getState().setCatalog([]);
    expect(useModelStore.getState().catalog).toEqual([]);
    expect(useModelStore.getState().catalogError).toBeNull();
  });

  it("setStatuses + setStatus", () => {
    useModelStore.getState().setStatuses({ a: "not_installed" }, true);
    expect(useModelStore.getState().ollamaRunning).toBe(true);
    useModelStore.getState().setStatus("a", "available");
    expect(useModelStore.getState().statuses).toEqual({ a: "available" });
  });

  it("download lifecycle: start → finish", () => {
    const st = useModelStore.getState();
    st.setDownloadProgress({
      name: "m",
      phase: "downloading",
      percent: 10,
      speed_mbps: 1,
      eta_seconds: 1,
      message: null,
    });
    st.startDownload("m");
    expect(useModelStore.getState().downloadingModel).toBe("m");
    expect(useModelStore.getState().downloadProgress).toBeNull();

    st.finishDownload("m", true);
    const after = useModelStore.getState();
    expect(after.downloadingModel).toBeNull();
    expect(after.downloadOutcome).toEqual({ name: "m", verified: true });
    expect(after.statuses.m).toBe("available");
  });

  it("download lifecycle: fail clears the in-progress model + sets the error", () => {
    const st = useModelStore.getState();
    st.startDownload("m");
    st.failDownload("m", "Not enough disk space");
    const after = useModelStore.getState();
    expect(after.downloadingModel).toBeNull();
    expect(after.downloadError).toBe("Not enough disk space");
  });

  it("clearDownloadFeedback wipes outcome + error", () => {
    const st = useModelStore.getState();
    st.finishDownload("m", false);
    st.failDownload("m", "x");
    st.clearDownloadFeedback();
    expect(useModelStore.getState().downloadOutcome).toBeNull();
    expect(useModelStore.getState().downloadError).toBeNull();
  });

  it("activate flips modelSetupRequired", () => {
    useModelStore.getState().activate("llama3.1:8b");
    expect(useModelStore.getState().activeModel).toBe("llama3.1:8b");
    expect(useModelStore.getState().modelSetupRequired).toBe(false);
  });
});
