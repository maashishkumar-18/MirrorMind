/**
 * Lightweight hand-written types for the backend lifecycle events the fe.1
 * shell listens for. fe.2 lands `ipc/schema/methods.ts` (the full zod mirror of
 * `src/common/ipc/methods.py`) and fe.4's typed IPC client supersedes this file
 * with schema-validated payloads.
 */

/** The `payload` of an `app.ready` event envelope (`AppReadyEvent` in methods.py). */
export interface AppReadyPayload {
  ipc_version: number;
  model_setup_required: boolean;
  active_model: string | null;
}

/** The `payload` of an `app.integrity_failed` event envelope. */
export interface AppIntegrityFailedPayload {
  details: string[];
}

/**
 * A raw IPC envelope as forwarded by the Rust shell over the `backend:message`
 * Tauri event, or returned by the `ipc_request` command. Only the fields the
 * fe.1/fe.3 glue inspects are typed; fe.4's client parses against the zod
 * schemas in `ipc/schema/methods.ts`.
 */
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

/** Payload of the `backend:exit` Tauri event (fe.3). */
export interface BackendExit {
  code: number | null;
  /** "previous_data_unrecoverable" | "restore_staged" | null (from exit 3 / 5). */
  reason: string | null;
  /** Set only when `reason === "restore_staged"`. */
  snapshot_path: string | null;
}

/**
 * The rejection value of the `ipc_request` command on a transport failure
 * (`Err(BridgeError)` in Rust). A well-formed backend `error` frame resolves as
 * a normal `RawEnvelope` instead.
 */
export interface BridgeError {
  kind: "timeout" | "backend_exited" | "backend_unavailable" | "transport";
  message: string;
}
