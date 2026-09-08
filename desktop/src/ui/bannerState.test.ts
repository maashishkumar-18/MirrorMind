import { describe, expect, it } from "vitest";

import type { BackendState } from "../store/backend";
import { selectBanner } from "./bannerState";

function state(over: Partial<BackendState>): BackendState {
  return {
    phase: "ready",
    ready: null,
    integrityDetails: [],
    lifecycle: null,
    lifecycleMessage: null,
    exit: null,
    lastError: null,
    versionMismatch: false,
    ipcError: null,
    restarting: false,
    ...over,
  } as BackendState;
}

describe("selectBanner", () => {
  it("null when healthy", () => {
    expect(selectBanner(state({}))).toBeNull();
  });

  it("recovery-mode on degraded", () => {
    expect(selectBanner(state({ phase: "degraded" }))?.variant).toBe("recovery-mode");
  });

  it("restoring while a restore relaunch is in flight", () => {
    const b = selectBanner(state({ restarting: true, lifecycle: "restore_staged" }));
    expect(b?.variant).toBe("restoring");
    expect(b?.message).toMatch(/backup/i);
  });

  it("a failed restore swap (phase 'exited') shows 'unavailable', not a stuck 'restoring'", () => {
    expect(
      selectBanner(
        state({
          phase: "exited",
          restarting: false,
          lifecycle: "restore_staged", // stale — the swap failed
          exit: { code: null, reason: "restore_failed", snapshot_path: null, will_retry: false },
        }),
      )?.variant,
    ).toBe("unavailable");
  });

  it("reconnecting 'restarting…' while the supervisor backs off", () => {
    const b = selectBanner(state({ restarting: true }));
    expect(b?.variant).toBe("reconnecting");
    expect(b?.message).toMatch(/restarting/i);
  });

  it("a give-up (phase 'exited') beats a lingering 'restarting' flag", () => {
    expect(
      selectBanner(
        state({
          phase: "exited",
          restarting: true, // even if this wasn't cleared
          exit: { code: null, reason: "supervisor_gave_up", snapshot_path: null, will_retry: false },
        }),
      )?.variant,
    ).toBe("unavailable");
  });

  it("unavailable on a plain exit", () => {
    expect(
      selectBanner(
        state({
          phase: "exited",
          exit: { code: 1, reason: null, snapshot_path: null, will_retry: false },
        }),
      )?.variant,
    ).toBe("unavailable");
  });

  it("reconnecting on a transient ipcError", () => {
    expect(selectBanner(state({ ipcError: "timeout" }))?.variant).toBe("reconnecting");
  });

  it("backend_exited + phase 'exited' → unavailable", () => {
    expect(
      selectBanner(state({ phase: "exited", ipcError: "backend_exited", exit: null }))?.variant,
    ).toBe("unavailable");
  });

  it("backend_exited while still starting → reconnecting", () => {
    expect(selectBanner(state({ phase: "starting", ipcError: "backend_exited" }))?.variant).toBe(
      "reconnecting",
    );
  });

  it("the gate suppresses the banner", () => {
    expect(selectBanner(state({ versionMismatch: true, phase: "degraded" }))).toBeNull();
  });
});
