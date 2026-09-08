import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import { invoke } from "@tauri-apps/api/core";

import { useBackendStore } from "../store/backend";
import { call, describeIpcError, ipcErrorUi, IpcCallError } from "./client";

const invokeMock = vi.mocked(invoke);
const backendInitial = useBackendStore.getState();

function responseEnvelope(method: string, result: Record<string, unknown>) {
  return {
    version: 1,
    message_type: "response" as const,
    request_id: "x",
    timestamp: "t",
    payload: { method, result },
  };
}

function errorEnvelope(code: string, message = "boom") {
  return {
    version: 1,
    message_type: "error" as const,
    request_id: "x",
    timestamp: "t",
    payload: { code, message },
  };
}

const VALID_STATUS = {
  ipc_version: 1,
  model_setup_required: true,
  active_model: null,
  last_exported_at: null,
  degraded: false,
  ready: true,
};

beforeEach(() => {
  useBackendStore.setState(backendInitial, true);
  invokeMock.mockReset();
});

describe("call()", () => {
  it("returns the zod-validated result on success", async () => {
    invokeMock.mockResolvedValue(responseEnvelope("app.status", VALID_STATUS));
    const result = await call("app.status", {});
    expect(result).toEqual(VALID_STATUS);
  });

  it("clears a prior ipcError on success", async () => {
    useBackendStore.setState({ ipcError: "timeout" });
    invokeMock.mockResolvedValue(responseEnvelope("app.status", VALID_STATUS));
    await call("app.status", {});
    expect(useBackendStore.getState().ipcError).toBeNull();
  });

  it("rejects invalid params before touching the bridge", async () => {
    await expect(call("model.download", { name: "" })).rejects.toMatchObject({
      kind: "schema",
      detail: { phase: "params" },
    });
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("maps a BridgeError rejection to a transport IpcCallError + store state", async () => {
    invokeMock.mockRejectedValue({ kind: "timeout", message: "no reply" });
    await expect(call("app.status", {})).rejects.toMatchObject({
      kind: "transport",
      detail: { transportKind: "timeout" },
    });
    expect(useBackendStore.getState().ipcError).toBe("timeout");
  });

  it("routes a version_mismatch error frame to the store flag (roadmap acceptance)", async () => {
    invokeMock.mockResolvedValue(errorEnvelope("version_mismatch", "got 2, expected 1"));
    await expect(call("app.status", {})).rejects.toMatchObject({
      kind: "backend",
      detail: { code: "version_mismatch" },
    });
    expect(useBackendStore.getState().versionMismatch).toBe(true);
  });

  it("surfaces a non-version-mismatch error frame as a backend IpcCallError", async () => {
    invokeMock.mockResolvedValue(errorEnvelope("no_model_active"));
    await expect(call("chat.send", { text: "hi" })).rejects.toMatchObject({
      kind: "backend",
      detail: { code: "no_model_active" },
    });
    expect(useBackendStore.getState().versionMismatch).toBe(false);
  });

  it("treats a schema-invalid result as a transport-ish schema error", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    invokeMock.mockResolvedValue(responseEnvelope("app.status", { ready: true })); // missing fields
    await expect(call("app.status", {})).rejects.toMatchObject({
      kind: "schema",
      detail: { phase: "result" },
    });
    expect(useBackendStore.getState().ipcError).toBe("transport");
  });

  it("passes the per-method timeout to the bridge (0 for model.download)", async () => {
    invokeMock.mockResolvedValue(
      responseEnvelope("model.download", {
        model_name: "m",
        status: "success",
        restarts: 0,
        resumes: 0,
        verified: true,
      }),
    );
    await call("model.download", { name: "mistral:latest" });
    expect(invokeMock.mock.calls[0][1]).toMatchObject({ timeoutMs: 0 });
  });
});

describe("describeIpcError()", () => {
  it("version_mismatch → please_restart", () => {
    const e = new IpcCallError("backend", "x", { code: "version_mismatch" });
    expect(describeIpcError(e, "ready")?.ui).toBe("please_restart");
  });

  it("timeout → temporarily_unavailable", () => {
    const e = new IpcCallError("transport", "x", { transportKind: "timeout" });
    expect(describeIpcError(e, "ready")?.ui).toBe("temporarily_unavailable");
  });

  it("backend_exited depends on phase", () => {
    const e = new IpcCallError("transport", "x", { transportKind: "backend_exited" });
    expect(describeIpcError(e, "exited")?.ui).toBe("unavailable");
    expect(describeIpcError(e, "starting")?.ui).toBe("temporarily_unavailable");
  });

  it("a plain backend error code is left to the caller", () => {
    const e = new IpcCallError("backend", "x", { code: "no_model_active" });
    expect(describeIpcError(e, "ready")).toBeNull();
  });

  it("a params schema error is not a banner", () => {
    const e = new IpcCallError("schema", "x", { phase: "params" });
    expect(describeIpcError(e, "ready")).toBeNull();
  });
});

describe("ipcErrorUi()", () => {
  it("backend_exited is only 'unavailable' once the phase is 'exited'", () => {
    expect(ipcErrorUi("backend_exited", "exited")).toBe("unavailable");
    expect(ipcErrorUi("backend_exited", "starting")).toBe("temporarily_unavailable");
  });
  it("every other kind is transient", () => {
    for (const k of ["timeout", "backend_unavailable", "transport"] as const) {
      expect(ipcErrorUi(k, "ready")).toBe("temporarily_unavailable");
    }
  });
});
