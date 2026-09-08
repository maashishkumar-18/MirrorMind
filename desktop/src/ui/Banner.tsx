import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import { useBackendStore } from "../store/backend";
import { selectBanner, type BannerVariant } from "./bannerState";

const ALERT_VARIANTS: ReadonlySet<string> = new Set(["unavailable", "recovery-mode"]);

export function Banner() {
  const state = useBackendStore(useShallow(selectBanner));
  const ipcError = useBackendStore((s) => s.ipcError);

  // "Reconnected" flash on the ipcError non-null → null edge.
  const prev = useRef<string | null>(ipcError);
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    const had = prev.current;
    prev.current = ipcError;
    if (had && !ipcError) {
      setFlash(true);
      const t = setTimeout(() => setFlash(false), 2500);
      return () => clearTimeout(t);
    }
  }, [ipcError]);

  // fe.7: the supervisor gave up — offer a manual restart.
  const [restarting, setRestarting] = useState(false);
  const restart = () => {
    setRestarting(true);
    void invoke("restart_backend")
      .catch(() => {})
      .finally(() => setRestarting(false));
  };

  let variant: BannerVariant | "ready" | "none" = "none";
  let message = "";
  if (state) {
    variant = state.variant;
    message = state.message;
  } else if (flash) {
    variant = "ready";
    message = "Reconnected";
  }

  const role = ALERT_VARIANTS.has(variant) ? "alert" : "status";

  return (
    <div className="banner" data-variant={variant} role={role} aria-live="polite">
      <span className="banner-msg">{message}</span>
      {variant === "reconnecting" && <span className="banner-dots" aria-hidden="true" />}
      {variant === "unavailable" && (
        <button type="button" className="banner-action" onClick={restart} disabled={restarting}>
          {restarting ? "Restarting…" : "Restart"}
        </button>
      )}
    </div>
  );
}
