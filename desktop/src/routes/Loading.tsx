import { useEffect, useState } from "react";

import { call } from "../ipc/client";
import { useBackendStore } from "../store/backend";
import { useModelStore } from "../store/model";

/**
 * fe.1/fe.3/fe.4 acceptance surface: the backend connection phase, the
 * zod-validated `app.status`, and the `version_mismatch` "please restart"
 * screen (roadmap 3.1). fe.5 replaces this with the real loading / degraded-mode
 * UI; fe.6 routes to the first-launch model flow.
 */
export function Loading() {
  const phase = useBackendStore((s) => s.phase);
  const ready = useBackendStore((s) => s.ready);
  const integrityDetails = useBackendStore((s) => s.integrityDetails);
  const exit = useBackendStore((s) => s.exit);
  const ipcError = useBackendStore((s) => s.ipcError);
  const versionMismatch = useBackendStore((s) => s.versionMismatch);
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

  if (versionMismatch) {
    return (
      <main className="screen">
        <h1>Please restart MirrorMind</h1>
        <p>
          The app and its backend are running different versions. Close and reopen MirrorMind to
          reconnect.
        </p>
      </main>
    );
  }

  return (
    <main className="screen">
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

      {ipcError && <p className="ipc-error">IPC error: {ipcError}</p>}

      {phase === "degraded" && (
        <ul className="integrity">
          {integrityDetails.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      )}

      {phase === "exited" && (
        <p className="exited">
          Backend exited ({exit?.reason ?? `code ${exit?.code ?? "unknown"}`}).
        </p>
      )}
    </main>
  );
}
