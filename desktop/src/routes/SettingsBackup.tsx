import { useEffect, useState } from "react";

import { call, IpcCallError } from "../ipc/client";
import { formatBytes } from "./modelRow";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { S } from "../strings";

function msg(e: unknown): string {
  return e instanceof IpcCallError ? e.message : String(e);
}

/** A backend exit during `backup.restore` is the expected outcome, not a failure. */
function isRestartUnderway(e: unknown): boolean {
  return (
    e instanceof IpcCallError &&
    e.kind === "transport" &&
    e.detail.transportKind === "backend_exited"
  );
}

interface Snapshot {
  path: string;
  created_at: string;
  size_bytes: number;
}

/**
 * Settings → Backup & Recovery (Phase 3 Step 3.4 — the Phase-2-deferred panel).
 * `backup.restore` stages the snapshot and the backend exits 5; the fe.7
 * supervisor swaps the file + relaunches and the fe.5 "Applying your backup…"
 * banner covers the rest.
 */
export function SettingsBackup() {
  const [snapshots, setSnapshots] = useState<Snapshot[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmPath, setConfirmPath] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [restarted, setRestarted] = useState(false);

  const load = () =>
    void call("backup.list", {})
      .then((r) => setSnapshots(r.backups))
      .catch((e) => setError(msg(e)));

  useEffect(load, []);

  const restore = () => {
    if (!confirmPath) return;
    setRestoring(true);
    void call("backup.restore", { path: confirmPath })
      .then(() => setRestarted(true))
      .catch((e) => {
        if (isRestartUnderway(e)) {
          setRestarted(true);
        } else {
          setError(msg(e));
          setRestoring(false);
          setConfirmPath(null);
        }
      });
  };

  if (restarted) {
    return (
      <section className="settings-panel">
        <h2>{S.settings.backup.title}</h2>
        <p className="settings-hint">{S.settings.backup.restarting}</p>
      </section>
    );
  }

  return (
    <section className="settings-panel">
      <h2>{S.settings.backup.title}</h2>
      <p className="settings-hint">
        MirrorMind keeps a rolling set of daily encrypted snapshots. Restoring one
        replaces your current data and restarts the app.
      </p>

      {error && <p className="first-run-error">{error}</p>}

      {snapshots === null ? (
        <p className="settings-hint">{S.settings.hintLoading}</p>
      ) : snapshots.length === 0 ? (
        <p className="settings-hint">{S.settings.backup.noSnapshots}</p>
      ) : (
        <ul className="settings-list">
          {snapshots.map((s) => (
            <li key={s.path} className="settings-row">
              <div className="settings-row-main">
                <div>{new Date(s.created_at).toLocaleString()}</div>
                <div className="settings-hint">{formatBytes(s.size_bytes)}</div>
              </div>
              <button type="button" onClick={() => setConfirmPath(s.path)}>
                Restore
              </button>
            </li>
          ))}
        </ul>
      )}

      {confirmPath && (
        <ConfirmDialog
          title={S.settings.backup.restoreTitle}
          body={
            <p>
              This replaces all of your current data with the contents of the
              selected snapshot and restarts MirrorMind. Anything created since
              that snapshot will be lost.
            </p>
          }
          confirmLabel={S.settings.backup.restoreConfirm}
          danger
          busy={restoring}
          onConfirm={restore}
          onCancel={() => setConfirmPath(null)}
        />
      )}
    </section>
  );
}
