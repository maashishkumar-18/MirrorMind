import { Navigate, Outlet } from "react-router-dom";

import { guardDecision } from "../routes/modelRow";
import { useBackendStore } from "../store/backend";
import { useModelStore } from "../store/model";
import { Starting } from "./Starting";

/** Blocks `/chat` (+ future feature routes) until a model is active. */
export function RequireModel() {
  const phase = useBackendStore((s) => s.phase);
  const need = useModelStore((s) => s.modelSetupRequired);

  switch (guardDecision(phase, need)) {
    case "loading":
      return <Starting />;
    case "first-run":
      return <Navigate to="/first-run" replace />;
    default:
      return <Outlet />;
  }
}
