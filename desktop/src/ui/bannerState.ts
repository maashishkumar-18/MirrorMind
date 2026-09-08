/**
 * The degraded-mode banner selector (Phase 3 Step 3.1-fe.5).
 *
 * A pure function over `BackendState`. `Banner.tsx` renders its output (plus a
 * transient "Reconnected" flash it tracks locally). The full-screen gate always
 * wins — when `selectAppGate` returns something, this returns `null`.
 */
import { ipcErrorUi } from "../ipc/client";
import type { BackendState } from "../store/backend";
import { selectAppGate } from "./gateState";

export type BannerVariant = "reconnecting" | "unavailable" | "recovery-mode" | "restoring";

export interface BannerState {
  variant: BannerVariant;
  message: string;
}

export function selectBanner(s: BackendState): BannerState | null {
  if (selectAppGate(s)) return null; // the gate covers everything — no banner behind it

  if (s.phase === "degraded") {
    return {
      variant: "recovery-mode",
      message: "MirrorMind is in recovery mode — restore a backup from Settings to continue.",
    };
  }

  // A restore relaunch is in flight (Rust emitted `restore_staged` with
  // will_retry, so `restarting` is set and `phase` has left "exited").
  if (s.restarting && s.lifecycle === "restore_staged") {
    return { variant: "restoring", message: "Applying your backup — MirrorMind will restart…" };
  }

  // A terminal exit wins over `restarting` — the supervisor gave up.
  if (s.phase === "exited") {
    return { variant: "unavailable", message: "MirrorMind's AI backend stopped." };
  }

  if (s.restarting) {
    return { variant: "reconnecting", message: "MirrorMind's backend stopped — restarting…" };
  }

  if (s.ipcError) {
    return ipcErrorUi(s.ipcError, s.phase) === "unavailable"
      ? { variant: "unavailable", message: "AI features are unavailable. Restart the app." }
      : { variant: "reconnecting", message: "AI features are temporarily unavailable — reconnecting" };
  }

  return null;
}
