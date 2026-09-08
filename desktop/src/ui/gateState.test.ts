import { describe, expect, it } from "vitest";

import type { BackendState } from "../store/backend";
import { PREVIOUS_DATA_FALLBACK, selectAppGate } from "./gateState";

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

describe("selectAppGate", () => {
  it("returns null when healthy", () => {
    expect(selectAppGate(state({}))).toBeNull();
  });

  it("gates on versionMismatch", () => {
    expect(selectAppGate(state({ versionMismatch: true }))?.kind).toBe("version-mismatch");
  });

  it("gates on a previous_data_unrecoverable exit and shows the backend message", () => {
    const g = selectAppGate(
      state({
        exit: { code: 3, reason: "previous_data_unrecoverable", snapshot_path: null },
        lifecycleMessage: "backend copy of the message",
      }),
    );
    expect(g?.kind).toBe("previous-data-unrecoverable");
    expect(g?.message).toBe("backend copy of the message");
  });

  it("falls back to the hardcoded message if the event was missed", () => {
    const g = selectAppGate(state({ lifecycle: "previous_data_unrecoverable" }));
    expect(g?.message).toBe(PREVIOUS_DATA_FALLBACK);
  });

  it("versionMismatch wins over previous-data", () => {
    const g = selectAppGate(
      state({ versionMismatch: true, lifecycle: "previous_data_unrecoverable" }),
    );
    expect(g?.kind).toBe("version-mismatch");
  });
});
