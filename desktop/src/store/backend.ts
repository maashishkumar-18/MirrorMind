import { create } from "zustand";

import type { AppReadyPayload, RawEnvelope } from "../ipc/events";

export type BackendPhase = "starting" | "ready" | "degraded" | "exited";

export interface BackendState {
  phase: BackendPhase;
  /** Payload of the last `app.ready` event, once seen. */
  ready: AppReadyPayload | null;
  /** Details from `app.integrity_failed`, if the backend came up degraded. */
  integrityDetails: string[];
  /** Exit code from `backend://exit` (null = unknown / signalled). */
  exitCode: number | null;
  /** The most recent raw envelope forwarded by the shell — fe.1 debug aid. */
  lastEnvelope: RawEnvelope | null;

  applyEnvelope: (envelope: RawEnvelope) => void;
  setExited: (code: number | null) => void;
}

export const useBackendStore = create<BackendState>((set) => ({
  phase: "starting",
  ready: null,
  integrityDetails: [],
  exitCode: null,
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

  setExited: (code) => set({ phase: "exited", exitCode: code }),
}));
