/**
 * Wires the Rust shell's `backend://` Tauri events into the Zustand store.
 * Called once from `main.tsx` before the first render. fe.4 replaces the raw
 * listener with the typed IPC client's schema-validated event stream.
 */
import { listen } from "@tauri-apps/api/event";

import type { RawEnvelope } from "./ipc/events";
import { useBackendStore } from "./store/backend";

export async function startBackendBridge(): Promise<void> {
  const { applyEnvelope, setExited } = useBackendStore.getState();

  await listen<RawEnvelope>("backend:message", (event) => {
    applyEnvelope(event.payload);
  });

  await listen<number | null>("backend:exit", (event) => {
    setExited(event.payload);
  });
}
