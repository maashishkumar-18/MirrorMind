/**
 * The raw stdio<->invoke bridge (Phase 3 Step 3.1-fe.3).
 *
 * `ipcRequest` builds a versioned `IPCEnvelope` request and hands it to the Rust
 * `ipc_request` command, which writes it to the backend's stdin and resolves
 * with the correlated `response` / `error` frame. Server-initiated events keep
 * arriving on the `backend:message` Tauri event (see `bootstrap.ts`).
 *
 * fe.4 wraps this with zod validation (`ipc/schema/methods.ts`), the per-method
 * timeout, and `version_mismatch` / timeout → store state. Keep this file thin.
 */
import { invoke } from "@tauri-apps/api/core";

import type { BridgeError, RawEnvelope } from "./events";

/** Mirror of `src/common/ipc/envelope.py::CURRENT_IPC_VERSION`. */
export const IPC_VERSION = 1;

// A monotonic per-process id — NOT crypto.randomUUID(): Tauri v2's
// http://tauri.localhost origin is not a guaranteed secure context on Windows,
// where crypto.randomUUID is undefined. Uniqueness within one backend process
// lifetime is all the correlation map needs.
let seq = 0;
export function nextRequestId(): string {
  return `fe-${Date.now().toString(36)}-${(seq++).toString(36)}`;
}

/**
 * Send one method call. Resolves with the raw response / error envelope;
 * rejects with a {@link BridgeError} on a transport failure (timeout, backend
 * gone). `timeoutMs = 0` disables the per-call timeout (Rust still enforces a
 * 15-minute ceiling) — for `model.download`.
 */
export async function ipcRequest(
  method: string,
  params: Record<string, unknown> = {},
  timeoutMs = 15000,
): Promise<RawEnvelope> {
  const envelope = {
    version: IPC_VERSION,
    message_type: "request" as const,
    request_id: nextRequestId(),
    timestamp: new Date().toISOString(),
    payload: { method, params },
  };
  return invoke<RawEnvelope>("ipc_request", { envelope, timeoutMs });
}

export type { BridgeError, RawEnvelope };
