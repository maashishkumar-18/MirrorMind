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
 * A raw IPC envelope as forwarded by the Rust shell over the `backend://message`
 * Tauri event. Only the fields fe.1 inspects are typed.
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
