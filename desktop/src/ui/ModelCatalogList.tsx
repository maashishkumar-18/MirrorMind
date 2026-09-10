import { formatBytes, formatEta, formatSpeed, rowAction } from "../routes/modelRow";
import { useModelStore } from "../store/model";
import { S } from "../strings";

/**
 * The bundled-model catalog list — rows with Download / Activate|Switch /
 * progress bar — shared by `/first-run` (fe.6) and Settings → Models (Step 3.4).
 * All the model state (`catalog` / `statuses` / download lifecycle) lives in
 * `useModelStore`, so both mounts stay in sync; the parent owns loading, the
 * activate/download handlers, and any navigation.
 */
export interface ModelCatalogListProps {
  /** "first-run" labels the action "Activate"; "settings" labels it "Switch"
   *  for an installed model that is not the active one. */
  variant: "first-run" | "settings";
  activatingName: string | null;
  onDownload: (name: string) => void;
  onActivate: (name: string) => void;
  onRetry: () => void;
}

export function ModelCatalogList({
  variant,
  activatingName,
  onDownload,
  onActivate,
  onRetry,
}: ModelCatalogListProps) {
  const catalog = useModelStore((s) => s.catalog);
  const catalogError = useModelStore((s) => s.catalogError);
  const statuses = useModelStore((s) => s.statuses);
  const ollamaRunning = useModelStore((s) => s.ollamaRunning);
  const downloadingModel = useModelStore((s) => s.downloadingModel);
  const downloadProgress = useModelStore((s) => s.downloadProgress);
  const downloadError = useModelStore((s) => s.downloadError);
  const downloadOutcome = useModelStore((s) => s.downloadOutcome);
  const clearDownloadFeedback = useModelStore((s) => s.clearDownloadFeedback);

  const rows = catalog
    ? [...catalog].sort(
        (a, b) => (b.recommended ? 1 : 0) - (a.recommended ? 1 : 0) || a.size_bytes - b.size_bytes,
      )
    : [];

  const activateLabel = variant === "settings" ? S.firstRun.switch : S.firstRun.activate;

  return (
    <>
      {ollamaRunning === false && catalog !== null && (
        <p className="first-run-notice">{S.firstRun.servicePaused}</p>
      )}

      {catalogError ? (
        <p className="first-run-error">
          {S.firstRun.catalogError}: {catalogError}{" "}
          <button type="button" onClick={onRetry}>
            {S.loading.retry}
          </button>
        </p>
      ) : catalog === null ? (
        <p>{S.firstRun.loadingModels}</p>
      ) : (
        <ul className="model-list">
          {rows.map((entry) => {
            const action = rowAction(statuses[entry.name]);
            const isThis = downloadingModel === entry.name;
            return (
              <li key={entry.name} className="model-row">
                <div className="model-row-head">
                  <strong>{entry.display_name}</strong>
                  {entry.recommended && <span className="pill">{S.firstRun.recommended}</span>}
                  <span className="model-row-meta">
                    {formatBytes(entry.size_bytes)} · {entry.min_ram_gb} {S.firstRun.ramSuffix}
                  </span>
                </div>
                <p className="model-row-desc">{entry.description}</p>

                {action === "download" && (
                  <button
                    type="button"
                    disabled={!!downloadingModel || !ollamaRunning}
                    onClick={() => onDownload(entry.name)}
                  >
                    {S.firstRun.download}
                  </button>
                )}
                {(action === "downloading" || isThis) && (
                  <div
                    className="progress"
                    role="progressbar"
                    aria-valuenow={downloadProgress?.percent ?? 0}
                  >
                    <div
                      className="progress-fill"
                      style={{
                        width: `${downloadProgress?.name === entry.name ? downloadProgress.percent : 0}%`,
                      }}
                    />
                    <span className="progress-label">
                      {downloadProgress?.name === entry.name
                        ? `${Math.round(downloadProgress.percent)}% · ${formatSpeed(downloadProgress.speed_mbps)} · ${formatEta(downloadProgress.eta_seconds)} · ${downloadProgress.phase}`
                        : S.firstRun.downloadStarting}
                    </span>
                  </div>
                )}
                {action === "activate" && !isThis && (
                  <button
                    type="button"
                    disabled={!!activatingName || !!downloadingModel}
                    onClick={() => onActivate(entry.name)}
                  >
                    {activatingName === entry.name ? S.firstRun.activating : activateLabel}
                  </button>
                )}
                {action === "active" && <span className="model-row-active">{S.firstRun.currentlyActive}</span>}
              </li>
            );
          })}
        </ul>
      )}

      {downloadOutcome && (
        <p className="first-run-outcome">
          ✓ {S.firstRun.downloaded(downloadOutcome.name)}
          {downloadOutcome.verified
            ? ` ${S.firstRun.integrityVerified}`
            : ` ${S.firstRun.integrityUnverified}`}{" "}
          <button type="button" onClick={clearDownloadFeedback}>
            {S.loading.dismiss}
          </button>
        </p>
      )}
      {downloadError && (
        <p className="first-run-error">
          {downloadError}{" "}
          <button type="button" onClick={clearDownloadFeedback}>
            {S.loading.dismiss}
          </button>
        </p>
      )}
    </>
  );
}
