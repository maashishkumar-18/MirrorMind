import { create } from "zustand";

import type { AppReadyPayload, BackendExit, RawEnvelope } from "../ipc/events";

export type BackendPhase = "starting" | "ready" | "degraded" | "exited";

export interface BackendState {
  phase: BackendPhase;
  /** Payload of the last `app.ready` event, once seen. */
  ready: AppReadyPayload | null;
  /** Details from `app.integrity_failed`, if the backend came up degraded. */
  integrityDetails: string[];
  /** The `backend:exit` payload, once the sidecar exits. */
  exit: BackendExit | null;
  /** The most recent unattributed backend `error` frame (`backend:error`). */
  lastError: RawEnvelope | null;
  /** The most recent raw envelope forwarded by the shell — fe.1 debug aid. */
  lastEnvelope: RawEnvelope | null;

  applyEnvelope: (envelope: RawEnvelope) => void;
  setExited: (exit: BackendExit) => void;
  setBackendError: (envelope: RawEnvelope) => void;
}

export const useBackendStore = create<BackendState>((set) => ({
  phase: "starting",
  ready: null,
  integrityDetails: [],
  exit: null,
  lastError: null,
  lastEnvelope: null,

  applyEnvelope: (envelope) =>
    set((state) => {
      const next: Partial<BackendState> = { lastEnvelope: envelope };
      if (envelope.message_type === "event") {
        const method = envelope.payload.method;
        const params = envelope.payload.params ?? {};
        if (method === "app.ready") {
          next.phase = state.phase === "exited" ? state.phase : "ready";
          next.ready = params as unknown as AppReadyPayload;
        } else if (method === "app.integrity_failed") {
          next.phase = "degraded";
          next.integrityDetails = (params.details as string[]) ?? [];
        }
      }
      return next;
    }),

  setExited: (exit) => set({ phase: "exited", exit }),
  setBackendError: (envelope) => set({ lastError: envelope }),
}));
