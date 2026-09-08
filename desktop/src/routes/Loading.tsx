import { Navigate } from "react-router-dom";

import { useBackendStore } from "../store/backend";
import { useModelStore } from "../store/model";
import { Starting } from "../ui/Starting";

/**
 * The `/` route: a splash while the backend comes up, then a redirect to
 * `/first-run` (no active model) or `/chat`. A `degraded` / `exited` backend
 * lands on `/chat` — the fe.5 banner / gate covers it; `model.*` calls needed by
 * `/first-run` fail in degraded mode anyway.
 */
export function Loading() {
  const phase = useBackendStore((s) => s.phase);
  const modelSetupRequired = useModelStore((s) => s.modelSetupRequired);

  if (phase === "starting") return <Starting />;
  const dest = phase === "ready" && modelSetupRequired ? "/first-run" : "/chat";
  return <Navigate to={dest} replace />;
}
