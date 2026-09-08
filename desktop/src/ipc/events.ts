/**
 * Typed backend-event layer (Phase 3 Step 3.1-fe.4).
 *
 * The Rust shell forwards every server-initiated envelope on a single
 * `backend:message` Tauri event. `subscribe(name, handler)` is a JS-side demux
 * over that stream: it looks the frame's `payload.method` up in `EVENT_SCHEMAS`,
 * zod-validates the payload, and fans out to the handlers registered for that
 * name. `backend:exit` / `backend:error` stay distinct Tauri events (wired in
 * `bootstrap.ts`).
 */
import { EVENT_SCHEMAS, type EventName } from "@ipc/methods";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { z } from "zod";

// Re-exported so stores keep a single import site for these shapes.
import type { AppReadyEvent, AppIntegrityFailedEvent } from "@ipc/methods";
export type AppReadyPayload = z.infer<typeof AppReadyEvent>;
export type AppIntegrityFailedPayload = z.infer<typeof AppIntegrityFailedEvent>;

/** A raw IPC envelope as forwarded by the Rust shell / returned by `ipc_request`. */
export interface RawEnvelope {
  version: number;
  message_type: "request" | "response" | "event" | "error";
  request_id: string;
  timestamp: string;
  payload: {
    method?: string;
    params?: Record<string, unknown>;
    result?: Record<string, unknown>;
    code?: string;
    message?: string;
  };
}

/**
 * Payload of the `backend:exit` Tauri event (fe.3 / fe.7). `will_retry` is true
 * while the supervisor is backing off toward a respawn — the UI shows a
 * "restarting…" banner and stays on the current route. `reason` is one of
 * `clean` / `crash` / `supervisor_gave_up` / `respawn_failed` /
 * `previous_data_unrecoverable` / `restore_staged` / `restore_failed`.
 */
export interface BackendExit {
  code: number | null;
  reason: string | null;
  snapshot_path: string | null;
  will_retry: boolean;
}

/** Rejection value of `ipc_request` on a transport failure (`Err(BridgeError)` in Rust). */
export interface BridgeError {
  kind: "timeout" | "backend_exited" | "backend_unavailable" | "transport";
  message: string;
}

type AnyHandler = (payload: unknown) => void;
const handlers = new Map<string, Set<AnyHandler>>();
let rawListener: Promise<UnlistenFn> | null = null;

function ensureRawListener(): void {
  if (rawListener) return;
  rawListener = listen<RawEnvelope>("backend:message", (event) => {
    const env = event.payload;
    if (env.message_type !== "event") return;
    const name = env.payload.method;
    if (!name) return;

    const schema = (EVENT_SCHEMAS as Record<string, z.ZodTypeAny>)[name];
    if (!schema) {
      console.warn(`[ipc] dropping unknown event: ${name}`);
      return;
    }
    const parsed = schema.safeParse(env.payload.params ?? {});
    if (!parsed.success) {
      console.error(`[ipc] event ${name} failed schema validation`, parsed.error);
      return;
    }
    handlers.get(name)?.forEach((h) => h(parsed.data));
  });
}

/**
 * Register a handler for one server-initiated event. Returns an unsubscribe
 * function. Payloads that fail `EVENT_SCHEMAS[name]` never reach the handler.
 */
export function subscribe<E extends EventName>(
  event: E,
  handler: (payload: z.infer<(typeof EVENT_SCHEMAS)[E]>) => void,
): () => void {
  ensureRawListener();
  const set = handlers.get(event) ?? new Set<AnyHandler>();
  set.add(handler as AnyHandler);
  handlers.set(event, set);
  return () => {
    handlers.get(event)?.delete(handler as AnyHandler);
  };
}

/** Test seam: drop every registration + the raw listener. */
export function _resetSubscriptions(): void {
  handlers.clear();
  void rawListener?.then((un) => un());
  rawListener = null;
}
