/**
 * Wires the Rust shell's `backend:*` Tauri events into the Zustand store.
 * Called once from `main.tsx` before the first render. fe.4 replaces the raw
 * `backend:message` listener with the typed IPC client's schema-validated
 * event stream.
 */
import { listen } from "@tauri-apps/api/event";

import type { BackendExit, RawEnvelope } from "./ipc/events";
import { useBackendStore } from "./store/backend";

export async function startBackendBridge(): Promise<void> {
  const { applyEnvelope, setExited, setBackendError } = useBackendStore.getState();

  await listen<RawEnvelope>("backend:message", (event) => {
    applyEnvelope(event.payload);
  });

  await listen<RawEnvelope>("backend:error", (event) => {
    setBackendError(event.payload);
  });

  await listen<BackendExit>("backend:exit", (event) => {
    setExited(event.payload);
  });
}
