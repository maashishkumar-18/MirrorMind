import { beforeEach, describe, expect, it } from "vitest";

import type { RawEnvelope } from "../ipc/events";
import { useBackendStore } from "./backend";

function event(method: string, params: Record<string, unknown>): RawEnvelope {
  return {
    version: 1,
    message_type: "event",
    request_id: "-",
    timestamp: "2026-09-08T00:00:00Z",
    payload: { method, params },
  };
}

const initial = useBackendStore.getState();

beforeEach(() => {
  useBackendStore.setState(initial, true);
});

describe("useBackendStore", () => {
  it("starts in the 'starting' phase", () => {
    expect(useBackendStore.getState().phase).toBe("starting");
  });

  it("moves to 'ready' and captures the app.ready payload", () => {
    useBackendStore.getState().applyEnvelope(
      event("app.ready", {
        ipc_version: 1,
        model_setup_required: true,
        active_model: null,
      }),
    );

    const state = useBackendStore.getState();
    expect(state.phase).toBe("ready");
    expect(state.ready).toEqual({
      ipc_version: 1,
      model_setup_required: true,
      active_model: null,
    });
  });

  it("moves to 'degraded' on app.integrity_failed and keeps the details", () => {
    useBackendStore
      .getState()
      .applyEnvelope(event("app.integrity_failed", { details: ["malformed page"] }));

    const state = useBackendStore.getState();
    expect(state.phase).toBe("degraded");
    expect(state.integrityDetails).toEqual(["malformed page"]);
  });

  it("stores the whole backend:exit payload and enters 'exited'", () => {
    useBackendStore
      .getState()
      .setExited({ code: 5, reason: "restore_staged", snapshot_path: "C:\\x.db" });

    const state = useBackendStore.getState();
    expect(state.phase).toBe("exited");
    expect(state.exit).toEqual({ code: 5, reason: "restore_staged", snapshot_path: "C:\\x.db" });
  });

  it("does not leave the 'exited' phase when a late app.ready arrives", () => {
    useBackendStore.getState().setExited({ code: 3, reason: "previous_data_unrecoverable", snapshot_path: null });
    useBackendStore.getState().applyEnvelope(
      event("app.ready", {
        ipc_version: 1,
        model_setup_required: false,
        active_model: "llama3.1:8b",
      }),
    );

    expect(useBackendStore.getState().phase).toBe("exited");
  });

  it("records the last unattributed backend error", () => {
    const errEnvelope: RawEnvelope = {
      version: 1,
      message_type: "error",
      request_id: "-",
      timestamp: "2026-09-08T00:00:00Z",
      payload: { code: "internal_error", message: "boom" },
    };
    useBackendStore.getState().setBackendError(errEnvelope);
    expect(useBackendStore.getState().lastError).toBe(errEnvelope);
  });

  it("records the last envelope for any message", () => {
    const envelope = event("app.reminders_pending", { overdue: [], pending_acknowledgment: [] });
    useBackendStore.getState().applyEnvelope(envelope);
    expect(useBackendStore.getState().lastEnvelope).toBe(envelope);
  });
});
