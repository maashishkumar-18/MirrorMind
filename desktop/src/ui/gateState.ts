/**
 * The full-screen blocker selector (Phase 3 Step 3.1-fe.5).
 *
 * A pure function over `BackendState` — no store, no render. `AppGate.tsx` is a
 * thin wrapper that renders whatever this returns.
 */
import type { BackendState } from "../store/backend";

/** Mirrors `src/security/errors.py::PreviousDataUnrecoverableError.MESSAGE`. Only used if the event was missed. */
export const PREVIOUS_DATA_FALLBACK =
  "Your previous data is protected by your Windows account and cannot be recovered after a Windows reinstall. Starting fresh.";

export interface AppGateState {
  kind: "version-mismatch" | "previous-data-unrecoverable";
  title: string;
  message: string;
}

export function selectAppGate(s: BackendState): AppGateState | null {
  if (s.versionMismatch) {
    return {
      kind: "version-mismatch",
      title: "Please restart MirrorMind",
      message:
        "The app and its backend are running different versions. Close and reopen MirrorMind to reconnect.",
    };
  }

  const previousDataLost =
    s.exit?.reason === "previous_data_unrecoverable" ||
    s.lifecycle === "previous_data_unrecoverable";
  if (previousDataLost) {
    return {
      kind: "previous-data-unrecoverable",
      title: "Starting fresh",
      message: s.lifecycleMessage ?? PREVIOUS_DATA_FALLBACK,
    };
  }

  return null;
}
