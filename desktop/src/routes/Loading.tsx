import { useEffect, useState } from "react";

import { call } from "../ipc/client";
import { useBackendStore } from "../store/backend";
import { useModelStore } from "../store/model";

/**
 * The `/` route. Backend connection phase + a zod-validated `app.status` probe.
 * The version-mismatch / previous-data gate and the degraded banner are now
 * `RootLayout` concerns (fe.5). fe.6 routes on to the first-launch model flow.
 */
export function Loading() {
  const phase = useBackendStore((s) => s.phase);
  const ready = useBackendStore((s) => s.ready);
  const activeModel = useModelStore((s) => s.activeModel);
  const modelSetupRequired = useModelStore((s) => s.modelSetupRequired);

  const [status, setStatus] = useState("…");
  useEffect(() => {
    let alive = true;
    call("app.status", {})
      .then((s) => {
        if (alive) setStatus(`degraded=${s.degraded} ready=${s.ready}`);
      })
      .catch(() => {
        if (alive) setStatus("failed (see banner)");
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="screen">
      <h1>MirrorMind</h1>
      <p className="phase">
        Backend: <strong data-testid="phase">{phase}</strong>
      </p>

      {phase === "ready" && ready && (
        <dl className="ready">
          <div>
            <dt>IPC version</dt>
            <dd>{ready.ipc_version}</dd>
          </div>
          <div>
            <dt>Model setup required</dt>
            <dd>{String(modelSetupRequired)}</dd>
          </div>
          <div>
            <dt>Active model</dt>
            <dd>{activeModel ?? "(none)"}</dd>
          </div>
        </dl>
      )}

      <p className="probe">
        <code>call(app.status)</code>: {status}
      </p>
    </div>
  );
}
