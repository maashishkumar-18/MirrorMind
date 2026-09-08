import { beforeEach, describe, expect, it, vi } from "vitest";

// Whole-module replacement, hoisted above the import — the real
// @tauri-apps/api/core touches Tauri internals at import time.
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import { invoke } from "@tauri-apps/api/core";

import { IPC_VERSION, ipcRequest, nextRequestId } from "./bridge";

const invokeMock = vi.mocked(invoke);

beforeEach(() => {
  invokeMock.mockReset();
  invokeMock.mockResolvedValue({
    version: 1,
    message_type: "response",
    request_id: "x",
    timestamp: "t",
    payload: {},
  });
});

describe("ipcRequest", () => {
  it("builds a v1 request envelope and forwards { envelope, timeoutMs } to invoke", async () => {
    await ipcRequest("app.status", { foo: 1 }, 5000);

    expect(invokeMock).toHaveBeenCalledTimes(1);
    const [command, args] = invokeMock.mock.calls[0] as [string, { envelope: Record<string, unknown>; timeoutMs: number }];
    expect(command).toBe("ipc_request");
    expect(args.timeoutMs).toBe(5000);

    const env = args.envelope;
    expect(env.version).toBe(IPC_VERSION);
    expect(env.message_type).toBe("request");
    expect(env.payload).toEqual({ method: "app.status", params: { foo: 1 } });
    expect(typeof env.request_id).toBe("string");
    expect(env.request_id).toMatch(/^fe-/);
    expect(new Date(env.timestamp as string).toISOString()).toBe(env.timestamp);
  });

  it("defaults params to {} and timeoutMs to 15000", async () => {
    await ipcRequest("health.check");
    const args = invokeMock.mock.calls[0][1] as { envelope: { payload: unknown }; timeoutMs: number };
    expect(args.timeoutMs).toBe(15000);
    expect((args.envelope.payload as { params: unknown }).params).toEqual({});
  });

  it("mints a distinct request_id per call", () => {
    expect(nextRequestId()).not.toBe(nextRequestId());
  });
});
