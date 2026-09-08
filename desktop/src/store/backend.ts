import type { AppReadyEvent } from "@ipc/methods";
import { create } from "zustand";
import type { z } from "zod";

import type { BackendExit, BridgeError, RawEnvelope } from "../ipc/events";

export type BackendPhase = "starting" | "ready" | "degraded" | "exited";
export type AppReady = z.infer<typeof AppReadyEvent>;

export interface BackendState {
  phase: BackendPhase;
  /** Payload of the last `app.ready` event, once seen. */
  ready: AppReady | null;
  /** Details from `app.integrity_failed`, if the backend came up degraded. */
  integrityDetails: string[];
  /** Last lifecycle reason (`previous_data_unrecoverable` / `restore_staged`). */
  lifecycle: string | null;
  /** The verbatim message from the last lifecycle event, if it carried one. */
  lifecycleMessage: string | null;
  /** The `backend:exit` payload, once the sidecar exits. */
  exit: BackendExit | null;
  /** The most recent unattributed backend `error` frame (`backend:error`). */
  lastError: RawEnvelope | null;

  /** IPC version disagreement — only a restart clears it. */
  versionMismatch: boolean;
  /** Transient transport failure from the last `call()` — cleared on success. */
  ipcError: BridgeError["kind"] | null;
  /** The supervisor is backing off toward a respawn (fe.7). Cleared by `app.ready`. */
  restarting: boolean;

  setReady: (payload: AppReady) => void;
  setDegraded: (details: string[]) => void;
  setLifecycle: (reason: string, message?: string) => void;
  setExited: (exit: BackendExit) => void;
  setBackendError: (envelope: RawEnvelope) => void;
  setVersionMismatch: () => void;
  setIpcError: (kind: BridgeError["kind"]) => void;
  clearIpcError: () => void;
  setRestarting: (v: boolean) => void;
}

export const useBackendStore = create<BackendState>((set) => ({
  phase: "starting",
  ready: null,
  integrityDetails: [],
  lifecycle: null,
  lifecycleMessage: null,
  exit: null,
  lastError: null,
  versionMismatch: false,
  ipcError: null,
  restarting: false,

  setReady: (payload) =>
    set((state) => ({
      ready: payload,
      restarting: false,
      // a backend that reached `app.ready` has no pending lifecycle transition
      lifecycle: null,
      lifecycleMessage: null,
      phase: state.phase === "exited" || state.phase === "degraded" ? state.phase : "ready",
    })),
  setDegraded: (details) => set({ phase: "degraded", integrityDetails: details }),
  setLifecycle: (reason, message) => set({ lifecycle: reason, lifecycleMessage: message ?? null }),
  setExited: (exit) => set({ phase: "exited", exit, restarting: false }),
  setBackendError: (envelope) => set({ lastError: envelope }),
  setVersionMismatch: () => set({ versionMismatch: true }),
  setIpcError: (kind) => set({ ipcError: kind }),
  clearIpcError: () => set({ ipcError: null }),
  // A relaunch supersedes a prior terminal exit — drop back to "starting" so the
  // next `app.ready` is allowed to promote `phase` to "ready" and clear the banner.
  setRestarting: (v) =>
    set((s) =>
      v
        ? { restarting: true, exit: null, phase: s.phase === "exited" ? "starting" : s.phase }
        : { restarting: false },
    ),
}));
