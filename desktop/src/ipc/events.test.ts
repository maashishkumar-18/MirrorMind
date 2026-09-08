import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type RawListener = (event: { payload: unknown }) => void;
let rawListener: RawListener | null = null;

vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async (_name: string, cb: RawListener) => {
    rawListener = cb;
    return () => {
      rawListener = null;
    };
  }),
}));

import { _resetSubscriptions, subscribe } from "./events";

function emit(method: string, params: unknown) {
  rawListener?.({
    payload: {
      version: 1,
      message_type: "event",
      request_id: "-",
      timestamp: "t",
      payload: { method, params },
    },
  });
}

beforeEach(() => {
  rawListener = null;
  _resetSubscriptions();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("subscribe()", () => {
  it("delivers a schema-valid event payload to the handler", () => {
    const handler = vi.fn();
    subscribe("app.ready", handler);
    emit("app.ready", { ipc_version: 1, model_setup_required: true, active_model: null });
    expect(handler).toHaveBeenCalledWith({
      ipc_version: 1,
      model_setup_required: true,
      active_model: null,
    });
  });

  it("drops a schema-invalid payload without calling the handler", () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const handler = vi.fn();
    subscribe("app.ready", handler);
    emit("app.ready", { ipc_version: "one" }); // wrong type + missing fields
    expect(handler).not.toHaveBeenCalled();
    expect(err).toHaveBeenCalled();
  });

  it("drops an unknown event name", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const handler = vi.fn();
    subscribe("app.ready", handler);
    emit("app.mystery", {});
    expect(handler).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalled();
  });

  it("stops delivering after unsubscribe", () => {
    const handler = vi.fn();
    const unsub = subscribe("app.ready", handler);
    unsub();
    emit("app.ready", { ipc_version: 1, model_setup_required: false, active_model: "m" });
    expect(handler).not.toHaveBeenCalled();
  });

  it("only wires one raw listener regardless of subscription count", async () => {
    const { listen } = await import("@tauri-apps/api/event");
    subscribe("app.ready", vi.fn());
    subscribe("app.integrity_failed", vi.fn());
    expect(vi.mocked(listen)).toHaveBeenCalledTimes(1);
  });
});
