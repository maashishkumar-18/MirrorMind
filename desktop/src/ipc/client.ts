/**
 * The typed IPC client (Phase 3 Step 3.1-fe.4).
 *
 * `call(method, params)` is the only way a component talks to the backend:
 *   1. zod-validate `params` against `METHOD_CONTRACTS[method].params`
 *   2. `ipcRequest` → the Rust bridge → correlated reply
 *   3. transport failure (`BridgeError`) → `IpcCallError { kind: "transport" }`
 *   4. backend `error` frame → `IpcCallError { kind: "backend" }`
 *      (`version_mismatch` also sets `useBackendStore.versionMismatch`)
 *   5. zod-validate the result against `METHOD_CONTRACTS[method].result`
 *
 * On any failure `call()` throws `IpcCallError` AND records global error state
 * on `useBackendStore` (so fe.5's banner can render it); on success it clears
 * the transient `ipcError`.
 */
import { METHOD_CONTRACTS, type MethodName } from "@ipc/methods";
import type { z, ZodTypeAny } from "zod";

import { useBackendStore } from "../store/backend";
import { useModelStore } from "../store/model";
import { ipcRequest } from "./bridge";
import type { BridgeError, RawEnvelope } from "./events";

/** `app.shutdown` is the Rust shell's job (see `graceful_shutdown`), never the frontend's. */
export type CallableMethod = Exclude<MethodName, "app.shutdown">;

/** Per-method request timeout (ms). 0 = no client timeout (Rust 15-min ceiling). */
const TIMEOUT_MS: Record<CallableMethod, number> = {
  "app.status": 5000,
  "health.check": 5000,
  "reminders.reconciliation": 5000,
  "model.catalog": 10000,
  "model.status": 15000, // hits Ollama's HTTP API a couple of times; slow-localhost margin
  "model.activate": 10000,
  "model.download": 0,
  "backup.list": 30000,
  "backup.restore": 30000,
  "chat.new": 15000,
  // chat.history waits on the worker thread — which may still be finishing
  // warm-up (embedding + cross-encoder model load) on a cold first launch.
  "chat.history": 30000,
  // confirm_action can run a slot-extraction LLM call + feature dispatch — same
  // budget as chat.send, not the old 10s.
  "chat.confirm_action": 120000,
  "chat.send": 120000,
  // feature views (Step 3.3) — plain SQLite CRUD on the worker thread
  "reminders.list": 10000,
  "reminders.complete": 10000,
  "reminders.dismiss": 10000,
  "reminders.reschedule": 10000,
  "reminders.update": 10000,
  "reminders.delete": 10000,
  "todos.list": 10000,
  "todos.complete": 10000,
  "todos.update": 10000,
  "todos.delete": 10000,
  "meetings.list": 10000,
  "meetings.get": 10000,
  "meetings.capture": 120000, // one local-LLM extraction pass
  "meetings.delete": 10000,
  "schedule.day": 10000,
  "schedule.week": 10000,
  "schedule.create_item": 10000,
  "schedule.update": 10000,
  "schedule.delete": 10000,
  // Settings & Diagnostics (Step 3.4)
  "settings.get": 5000,
  "settings.update": 5000,
  "data.info": 5000,
  "data.export": 120000, // streams every message row on the worker thread
  "data.wipe": 60000,
  "diagnostics.logs": 10000,
  "diagnostics.metrics": 10000,
  "diagnostics.report": 30000, // reads + redacts the rotating log
};

export type IpcErrorKind = "transport" | "backend" | "schema";

export class IpcCallError extends Error {
  readonly kind: IpcErrorKind;
  readonly detail: { transportKind?: BridgeError["kind"]; code?: string; phase?: "params" | "result" };

  constructor(
    kind: IpcErrorKind,
    message: string,
    detail: IpcCallError["detail"] = {},
  ) {
    super(message);
    this.name = "IpcCallError";
    this.kind = kind;
    this.detail = detail;
  }
}

function isBridgeError(x: unknown): x is BridgeError {
  return (
    typeof x === "object" &&
    x !== null &&
    "kind" in x &&
    "message" in x &&
    typeof (x as { kind: unknown }).kind === "string"
  );
}

export async function call<M extends CallableMethod>(
  method: M,
  params: z.input<(typeof METHOD_CONTRACTS)[M]["params"]>,
  options: { timeoutMs?: number } = {},
): Promise<z.infer<(typeof METHOD_CONTRACTS)[M]["result"]>> {
  const contract = METHOD_CONTRACTS[method];

  // 1. params — a failure here is a frontend bug; loud, but no UI-state change.
  const p = (contract.params as ZodTypeAny).safeParse(params);
  if (!p.success) {
    throw new IpcCallError("schema", `invalid params for ${method}: ${p.error.message}`, {
      phase: "params",
    });
  }

  // 2. transport
  let envelope: RawEnvelope;
  try {
    envelope = await ipcRequest(
      method,
      p.data as Record<string, unknown>,
      options.timeoutMs ?? TIMEOUT_MS[method],
    );
  } catch (err) {
    if (isBridgeError(err)) {
      useBackendStore.getState().setIpcError(err.kind);
      throw new IpcCallError("transport", err.message, { transportKind: err.kind });
    }
    useBackendStore.getState().setIpcError("transport");
    throw new IpcCallError("transport", String(err));
  }

  // 3. backend error frame (checked BEFORE result validation)
  if (envelope.message_type === "error") {
    const code = envelope.payload.code ?? "unknown";
    if (code === "version_mismatch") useBackendStore.getState().setVersionMismatch();
    if (code === "no_model_active") useModelStore.setState({ modelSetupRequired: true });
    throw new IpcCallError("backend", envelope.payload.message ?? code, { code });
  }

  // 4. result — a failure here is backend schema drift.
  const r = (contract.result as ZodTypeAny).safeParse(envelope.payload.result ?? {});
  if (!r.success) {
    if (import.meta.env.DEV) console.error(`[ipc] ${method} result failed validation`, r.error);
    useBackendStore.getState().setIpcError("transport");
    throw new IpcCallError("schema", `${method} result failed schema validation`, {
      phase: "result",
    });
  }

  useBackendStore.getState().clearIpcError();
  return r.data as z.infer<(typeof METHOD_CONTRACTS)[M]["result"]>;
}

export type IpcErrorUi = "please_restart" | "temporarily_unavailable" | "unavailable";

/**
 * A transport-class failure's severity, given the current backend phase. Shared
 * by `describeIpcError` (a caught error) and `selectBanner` (the stored
 * `ipcError` kind) so the two agree.
 */
export function ipcErrorUi(
  kind: BridgeError["kind"],
  phase: string,
): "temporarily_unavailable" | "unavailable" {
  if (kind === "backend_exited" && phase === "exited") return "unavailable";
  return "temporarily_unavailable";
}

/**
 * Map an `IpcCallError` to a global UI state, given the current backend phase.
 * Returns `null` for a method-specific backend error the caller should handle
 * itself (e.g. `no_model_active`).
 */
export function describeIpcError(
  err: IpcCallError,
  phase: string,
): { ui: IpcErrorUi; message: string } | null {
  if (err.kind === "backend") {
    if (err.detail.code === "version_mismatch") {
      return { ui: "please_restart", message: "MirrorMind needs a restart to reconnect." };
    }
    return null;
  }
  if (err.kind === "schema") {
    if (err.detail.phase === "params") return null; // frontend bug — not a UI banner
    return { ui: "temporarily_unavailable", message: "AI features are temporarily unavailable." };
  }
  // transport
  const ui = ipcErrorUi(err.detail.transportKind ?? "transport", phase);
  return ui === "unavailable"
    ? { ui, message: "AI features are unavailable." }
    : { ui, message: "AI features are temporarily unavailable — reconnecting…" };
}
