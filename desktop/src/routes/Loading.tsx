import { useBackendStore } from "../store/backend";

/**
 * fe.1 acceptance surface: renders the backend connection phase and the raw
 * `app.ready` payload, proving the sidecar stdout pipe works end to end.
 * fe.5 replaces this with the real loading / degraded-mode UI, and fe.6 routes
 * to the first-launch model flow when `model_setup_required` is true.
 */
export function Loading() {
  const phase = useBackendStore((s) => s.phase);
  const ready = useBackendStore((s) => s.ready);
  const integrityDetails = useBackendStore((s) => s.integrityDetails);
  const exitCode = useBackendStore((s) => s.exitCode);
  const lastEnvelope = useBackendStore((s) => s.lastEnvelope);

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

      {phase === "degraded" && (
        <ul className="integrity">
          {integrityDetails.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      )}

      {phase === "exited" && <p className="exited">Backend exited (code {exitCode ?? "unknown"}).</p>}

      {lastEnvelope && (
        <details className="raw">
          <summary>Last envelope</summary>
          <pre>{JSON.stringify(lastEnvelope, null, 2)}</pre>
        </details>
      )}
    </main>
  );
}
