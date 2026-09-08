import { useEffect, useState } from "react";

import { ipcRequest } from "../ipc/bridge";
import { useBackendStore } from "../store/backend";

/**
 * fe.1/fe.3 acceptance surface: renders the backend connection phase, the raw
 * `app.ready` payload, and one `ipc_request` round-trip (`app.status`) — proving
 * both the event stream and the request/response bridge end to end. fe.5
 * replaces this with the real loading / degraded-mode UI; fe.6 routes to the
 * first-launch model flow when `model_setup_required` is true.
 */
export function Loading() {
  const phase = useBackendStore((s) => s.phase);
  const ready = useBackendStore((s) => s.ready);
  const integrityDetails = useBackendStore((s) => s.integrityDetails);
  const exit = useBackendStore((s) => s.exit);
  const lastEnvelope = useBackendStore((s) => s.lastEnvelope);

  const [probe, setProbe] = useState("…");
  useEffect(() => {
    let alive = true;
    ipcRequest("app.status", {}, 5000)
      .then((env) => {
        if (alive) setProbe(`ok — ${JSON.stringify(env.payload.result)}`);
      })
      .catch((err) => {
        if (alive) setProbe(`error — ${JSON.stringify(err)}`);
      });
    return () => {
      alive = false;
    };
  }, []);

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
            <dd>{String(ready.model_setup_required)}</dd>
          </div>
          <div>
            <dt>Active model</dt>
            <dd>{ready.active_model ?? "(none)"}</dd>
          </div>
        </dl>
      )}

      <p className="probe">
        <code>ipc_request(app.status)</code>: {probe}
      </p>

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

      {lastEnvelope && (
        <details className="raw">
          <summary>Last envelope</summary>
          <pre>{JSON.stringify(lastEnvelope, null, 2)}</pre>
        </details>
      )}
    </main>
  );
}
