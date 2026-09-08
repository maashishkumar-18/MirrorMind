import { useShallow } from "zustand/react/shallow";

import { useBackendStore } from "../store/backend";
import { selectAppGate } from "./gateState";

export function AppGate() {
  const gate = useBackendStore(useShallow(selectAppGate));
  if (!gate) return null;

  return (
    <div
      className="app-gate"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="app-gate-title"
    >
      <div className="app-gate-card">
        <h1 id="app-gate-title">{gate.title}</h1>
        <p>{gate.message}</p>
        <p className="app-gate-hint">Close and reopen MirrorMind.</p>
      </div>
    </div>
  );
}
