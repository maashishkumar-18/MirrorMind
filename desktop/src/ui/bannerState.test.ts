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

  it("restoring on a restore_staged exit — even though phase is 'exited'", () => {
    expect(
      selectBanner(
        state({
          phase: "exited",
          exit: { code: 5, reason: "restore_staged", snapshot_path: "C:\\x.db" },
        }),
      )?.variant,
    ).toBe("restoring");
  });

  it("unavailable on a plain exit", () => {
    expect(
      selectBanner(state({ phase: "exited", exit: { code: 1, reason: null, snapshot_path: null } }))
        ?.variant,
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
