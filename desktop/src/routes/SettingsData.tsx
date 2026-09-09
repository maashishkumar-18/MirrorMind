import { save } from "@tauri-apps/plugin-dialog";
import { useEffect, useState } from "react";

import { call, IpcCallError } from "../ipc/client";
import { useSettingsStore } from "../store/settings";
import { ConfirmDialog } from "../ui/ConfirmDialog";

function msg(e: unknown): string {
  return e instanceof IpcCallError ? e.message : String(e);
}

interface Info {
  last_exported_at: string | null;
  needs_export: boolean;
  settings_line: string;
  uninstall_warning: string;
  export_blurb: string;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Settings → Data & Privacy (Phase 3 Step 3.4 — folds in the Phase-2-deferred
 * export badge + full-wipe confirm). Backend-authoritative copy strings from
 * `data.info`; the native save dialog picks the export path.
 */
export function SettingsData() {
  const [info, setInfo] = useState<Info | null>(null);
  const [status, setStatus] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [exporting, setExporting] = useState(false);
  const [confirmWipe, setConfirmWipe] = useState(false);
  const [wiping, setWiping] = useState(false);

  const load = () =>
    void call("data.info", {})
      .then((r) => {
        setInfo(r);
        useSettingsStore.getState().setLastExportedAt(r.last_exported_at);
      })
      .catch((e) => setStatus({ kind: "err", text: msg(e) }));

  useEffect(load, []);

  const exportData = async () => {
    setStatus(null);
    let path: string | null;
    try {
      path = await save({
        defaultPath: `mirrormind-export-${today()}.json`,
        filters: [{ name: "JSON", extensions: ["json"] }],
      });
    } catch (e) {
      setStatus({ kind: "err", text: msg(e) });
      return;
    }
    if (!path) return; // user cancelled the dialog
    setExporting(true);
    void call("data.export", { path })
      .then((r) => {
        useSettingsStore.getState().setLastExportedAt(r.exported_at);
        setStatus({ kind: "ok", text: `Exported ${r.bytes_written.toLocaleString()} bytes.` });
        load();
      })
      .catch((e) => setStatus({ kind: "err", text: msg(e) }))
      .finally(() => setExporting(false));
  };

  const wipe = () => {
    setWiping(true);
    void call("data.wipe", { confirm: true })
      .then(() => {
        // The backend reset its own state; reload the webview so every store
        // re-hydrates from a clean app.status.
        window.location.reload();
      })
      .catch((e) => {
        setStatus({ kind: "err", text: msg(e) });
        setWiping(false);
        setConfirmWipe(false);
      });
  };

  return (
    <section className="settings-panel">
      <h2>Data &amp; Privacy</h2>

      {info ? (
        <>
          <p className="settings-hint">{info.settings_line}</p>
          <p className="settings-hint">{info.export_blurb}</p>

          <div className="settings-actions">
            <button type="button" disabled={exporting} onClick={() => void exportData()}>
              {exporting ? "Exporting…" : "Export all data"}
            </button>
            {status && (
              <span className={status.kind === "ok" ? "settings-ok" : "first-run-error"}>
                {status.text}
              </span>
            )}
          </div>

          <p className="settings-hint settings-danger" style={{ marginTop: "1.5rem" }}>
            {info.uninstall_warning}
          </p>
          <button
            type="button"
            className="settings-danger"
            onClick={() => setConfirmWipe(true)}
          >
            Delete all my data
          </button>
        </>
      ) : status?.kind === "err" ? (
        <p className="first-run-error">{status.text}</p>
      ) : (
        <p className="settings-hint">Loading…</p>
      )}

      {confirmWipe && (
        <ConfirmDialog
          title="Delete all your data?"
          body={
            <p>
              This soft-deletes every reminder, to-do, meeting note, schedule entry,
              conversation, and summary. Your active model is kept. This cannot be
              undone — export first if you might want the data back.
            </p>
          }
          confirmLabel="Delete everything"
          danger
          busy={wiping}
          onConfirm={wipe}
          onCancel={() => setConfirmWipe(false)}
        />
      )}
    </section>
  );
}
