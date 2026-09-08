import { beforeEach, describe, expect, it } from "vitest";

import { useBackendStore } from "./backend";

const initial = useBackendStore.getState();

beforeEach(() => {
  useBackendStore.setState(initial, true);
});

describe("useBackendStore", () => {
  it("starts in the 'starting' phase", () => {
    expect(useBackendStore.getState().phase).toBe("starting");
  });

  it("setReady moves to 'ready' and keeps the payload", () => {
    useBackendStore
      .getState()
      .setReady({ ipc_version: 1, model_setup_required: true, active_model: null });
    const s = useBackendStore.getState();
    expect(s.phase).toBe("ready");
    expect(s.ready?.model_setup_required).toBe(true);
  });

  it("setDegraded moves to 'degraded' and keeps details", () => {
    useBackendStore.getState().setDegraded(["malformed page"]);
    const s = useBackendStore.getState();
    expect(s.phase).toBe("degraded");
    expect(s.integrityDetails).toEqual(["malformed page"]);
  });

  it("setReady does not override 'exited' or 'degraded'", () => {
    useBackendStore.getState().setExited({ code: 3, reason: "x", snapshot_path: null });
    useBackendStore
      .getState()
      .setReady({ ipc_version: 1, model_setup_required: false, active_model: "m" });
    expect(useBackendStore.getState().phase).toBe("exited");
  });

  it("stores the whole backend:exit payload", () => {
    useBackendStore
      .getState()
      .setExited({ code: 5, reason: "restore_staged", snapshot_path: "C:\\x.db" });
    expect(useBackendStore.getState().exit).toEqual({
      code: 5,
      reason: "restore_staged",
      snapshot_path: "C:\\x.db",
    });
  });

  it("versionMismatch is sticky; ipcError clears", () => {
    const st = useBackendStore.getState();
    st.setVersionMismatch();
    st.setIpcError("timeout");
    expect(useBackendStore.getState().versionMismatch).toBe(true);
    expect(useBackendStore.getState().ipcError).toBe("timeout");
    st.clearIpcError();
    expect(useBackendStore.getState().ipcError).toBeNull();
    expect(useBackendStore.getState().versionMismatch).toBe(true);
  });
});
