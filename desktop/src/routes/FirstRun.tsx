import { useCallback, useEffect, useReducer } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { call, IpcCallError } from "../ipc/client";
import { useBackendStore } from "../store/backend";
import { useModelStore } from "../store/model";
import { Starting } from "../ui/Starting";
import { firstRunInitial, firstRunReducer } from "./firstRunReducer";
import { formatBytes, formatEta, formatSpeed, rowAction } from "./modelRow";

function msg(e: unknown): string {
  return e instanceof IpcCallError ? e.message : String(e);
}

export function FirstRun() {
  const phase = useBackendStore((s) => s.phase);
  const navigate = useNavigate();

  const modelSetupRequired = useModelStore((s) => s.modelSetupRequired);
  const catalog = useModelStore((s) => s.catalog);
  const catalogError = useModelStore((s) => s.catalogError);
  const statuses = useModelStore((s) => s.statuses);
  const ollamaRunning = useModelStore((s) => s.ollamaRunning);
  const downloadingModel = useModelStore((s) => s.downloadingModel);
  const downloadProgress = useModelStore((s) => s.downloadProgress);
  const downloadError = useModelStore((s) => s.downloadError);
  const downloadOutcome = useModelStore((s) => s.downloadOutcome);

  const [s, dispatch] = useReducer(firstRunReducer, firstRunInitial);

  const load = useCallback(async () => {
    useModelStore.setState({ catalogError: null });
    // Independent — a flaky model.status must not hide the catalog.
    const [cat, st] = await Promise.allSettled([
      call("model.catalog", {}),
      call("model.status", {}),
    ]);
    if (cat.status === "fulfilled") useModelStore.getState().setCatalog(cat.value.models);
    else useModelStore.getState().setCatalogError(msg(cat.reason));
    if (st.status === "fulfilled")
      useModelStore.getState().setStatuses(st.value.statuses, st.value.ollama_running);
  }, []);

  useEffect(() => {
    if (phase === "ready" && modelSetupRequired) void load();
  }, [phase, modelSetupRequired, load]);

  if (phase === "starting") return <Starting />;
  if (!modelSetupRequired) return <Navigate to="/chat" replace />;
  if (phase === "degraded") {
    return (
      <div className="screen">
        <h1>Recovery mode</h1>
        <p>MirrorMind needs a backup restored before you can set up a model.</p>
      </div>
    );
  }

  const startDownload = (name: string) => {
    useModelStore.getState().startDownload(name);
    void call("model.download", { name }, { timeoutMs: 0 })
      .then((r) => useModelStore.getState().finishDownload(name, r.verified))
      .catch((e) => useModelStore.getState().failDownload(name, msg(e)));
  };

  const activate = (name: string) => {
    dispatch({ type: "activate/start", name });
    void call("model.activate", { name })
      .then((r) => {
        useModelStore.getState().activate(r.active_model);
        navigate("/chat", { replace: true });
      })
      .catch((e) => dispatch({ type: "activate/fail", message: msg(e) }));
  };

  const rows = catalog
    ? [...catalog].sort(
        (a, b) =>
          (b.recommended ? 1 : 0) - (a.recommended ? 1 : 0) || a.size_bytes - b.size_bytes,
      )
    : [];

  return (
    <div className="screen">
      <h1>Choose a model</h1>
      <p>Pick a model to download. You can add or switch models later in Settings.</p>

      {ollamaRunning === false && catalog !== null && (
        <p className="first-run-notice">The model service isn't running — downloads are paused.</p>
      )}

      {catalogError ? (
        <p className="first-run-error">
          Couldn't load the model list: {catalogError}{" "}
          <button type="button" onClick={() => void load()}>
            Retry
          </button>
        </p>
      ) : catalog === null ? (
        <p>Loading models…</p>
      ) : (
        <ul className="model-list">
          {rows.map((entry) => {
            const action = rowAction(statuses[entry.name]);
            const isThis = downloadingModel === entry.name;
            return (
              <li key={entry.name} className="model-row">
                <div className="model-row-head">
                  <strong>{entry.display_name}</strong>
                  {entry.recommended && <span className="pill">Recommended</span>}
                  <span className="model-row-meta">
                    {formatBytes(entry.size_bytes)} · {entry.min_ram_gb} GB RAM
                  </span>
                </div>
                <p className="model-row-desc">{entry.description}</p>

                {action === "download" && (
                  <button
                    type="button"
                    disabled={!!downloadingModel || !ollamaRunning}
                    onClick={() => startDownload(entry.name)}
                  >
                    Download
                  </button>
                )}
                {(action === "downloading" || isThis) && (
                  <div className="progress" role="progressbar" aria-valuenow={downloadProgress?.percent ?? 0}>
                    <div
                      className="progress-fill"
                      style={{ width: `${downloadProgress?.name === entry.name ? downloadProgress.percent : 0}%` }}
                    />
                    <span className="progress-label">
                      {downloadProgress?.name === entry.name
                        ? `${Math.round(downloadProgress.percent)}% · ${formatSpeed(downloadProgress.speed_mbps)} · ${formatEta(downloadProgress.eta_seconds)} · ${downloadProgress.phase}`
                        : "starting…"}
                    </span>
                  </div>
                )}
                {action === "activate" && !isThis && (
                  <button
                    type="button"
                    disabled={!!s.activating || !!downloadingModel}
                    onClick={() => activate(entry.name)}
                  >
                    {s.activating === entry.name ? "Activating…" : "Activate"}
                  </button>
                )}
                {action === "active" && <span className="model-row-active">Currently active</span>}
              </li>
            );
          })}
        </ul>
      )}

      {downloadOutcome && (
        <p className="first-run-outcome">
          ✓ Downloaded {downloadOutcome.name}
          {downloadOutcome.verified ? " · integrity verified" : " · couldn't verify — try re-downloading"}{" "}
          <button type="button" onClick={() => useModelStore.getState().clearDownloadFeedback()}>
            Dismiss
          </button>
        </p>
      )}
      {downloadError && (
        <p className="first-run-error">
          {downloadError}{" "}
          <button type="button" onClick={() => useModelStore.getState().clearDownloadFeedback()}>
            Dismiss
          </button>
        </p>
      )}
      {s.activateError && (
        <p className="first-run-error">
          {s.activateError}{" "}
          <button type="button" onClick={() => dispatch({ type: "activate/reset" })}>
            Dismiss
          </button>
        </p>
      )}
    </div>
  );
}
