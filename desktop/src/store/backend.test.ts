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

  it("setReady does not override 'exited' or 'degraded' but clears 'restarting'", () => {
    useBackendStore.getState().setRestarting(true);
    useBackendStore.getState().setExited({ code: 3, reason: "x", snapshot_path: null, will_retry: false });
    useBackendStore
      .getState()
      .setReady({ ipc_version: 1, model_setup_required: false, active_model: "m" });
    expect(useBackendStore.getState().phase).toBe("exited");
    expect(useBackendStore.getState().restarting).toBe(false);
  });

  it("setRestarting toggles; setReady clears it", () => {
    useBackendStore.getState().setRestarting(true);
    expect(useBackendStore.getState().restarting).toBe(true);
    useBackendStore
      .getState()
      .setReady({ ipc_version: 1, model_setup_required: false, active_model: "m" });
    expect(useBackendStore.getState().restarting).toBe(false);
  });

  it("setRestarting(true) supersedes a terminal exit so the next app.ready recovers", () => {
    useBackendStore
      .getState()
      .setExited({ code: null, reason: "respawn_failed", snapshot_path: null, will_retry: false });
    expect(useBackendStore.getState().phase).toBe("exited");

    // the user clicked "Restart" — Rust emits backend:exit { will_retry: true }
    useBackendStore.getState().setRestarting(true);
    expect(useBackendStore.getState().phase).toBe("starting");
    expect(useBackendStore.getState().exit).toBeNull();

    useBackendStore
      .getState()
      .setReady({ ipc_version: 1, model_setup_required: false, active_model: "m" });
    expect(useBackendStore.getState().phase).toBe("ready");
    expect(useBackendStore.getState().restarting).toBe(false);
  });

  it("stores the whole backend:exit payload", () => {
    useBackendStore
      .getState()
      .setExited({ code: 5, reason: "restore_staged", snapshot_path: "C:\\x.db", will_retry: false });
    expect(useBackendStore.getState().exit).toEqual({
      code: 5,
      reason: "restore_staged",
      snapshot_path: "C:\\x.db",
      will_retry: false,
    });
  });

  it("setReady clears a pending lifecycle transition (restore relaunch reached ready)", () => {
    useBackendStore.getState().setLifecycle("restore_staged");
    useBackendStore.getState().setRestarting(true);
    useBackendStore
      .getState()
      .setReady({ ipc_version: 1, model_setup_required: false, active_model: "m" });
    const s = useBackendStore.getState();
    expect(s.lifecycle).toBeNull();
    expect(s.restarting).toBe(false);
    expect(s.phase).toBe("ready");
  });

  it("setLifecycle stores the reason and the optional message", () => {
    useBackendStore.getState().setLifecycle("previous_data_unrecoverable", "starting fresh");
    expect(useBackendStore.getState().lifecycle).toBe("previous_data_unrecoverable");
    expect(useBackendStore.getState().lifecycleMessage).toBe("starting fresh");
    useBackendStore.getState().setLifecycle("restore_staged");
    expect(useBackendStore.getState().lifecycleMessage).toBeNull();
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
