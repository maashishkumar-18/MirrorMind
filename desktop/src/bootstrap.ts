/**
 * Wires the backend's events into the Zustand stores (Phase 3 Step 3.1-fe.4).
 * Called once from `App`'s `useEffect`; returns a teardown that removes every
 * registration so an HMR remount does not double-subscribe.
 */
import { listen } from "@tauri-apps/api/event";

import { call, IpcCallError } from "./ipc/client";
import { subscribe, type BackendExit, type RawEnvelope } from "./ipc/events";
import { useBackendStore } from "./store/backend";
import { useModelStore } from "./store/model";
import { useReminderStore } from "./store/reminders";

export async function startBackendBridge(): Promise<() => void> {
  const backend = useBackendStore.getState;
  const model = useModelStore.getState;
  const reminders = useReminderStore.getState;

  const unsubs = [
    subscribe("app.ready", (p) => {
      backend().setReady(p);
      model().hydrateFromReady(p);
    }),
    subscribe("app.integrity_failed", (p) => backend().setDegraded(p.details)),
    subscribe("app.previous_data_unrecoverable", (p) =>
      backend().setLifecycle("previous_data_unrecoverable", p.message),
    ),
    subscribe("app.restore_staged", () => backend().setLifecycle("restore_staged")),
    subscribe("app.reminders_pending", (p) => reminders().setReconciliation(p)),
    subscribe("model.download.progress", (p) => model().setDownloadProgress(p)),
  ];

  // `backend:exit` / `backend:error` are distinct Tauri events, not `backend:message` frames.
  const unlistenExit = await listen<BackendExit>("backend:exit", (e) => backend().setExited(e.payload));
  const unlistenError = await listen<RawEnvelope>("backend:error", (e) =>
    backend().setBackendError(e.payload),
  );

  // Recover a missed `app.ready` / `app.integrity_failed` (event fired before
  // the listener attached).
  call("app.status", {})
    .then((status) => {
      backend().setReady({
        ipc_version: status.ipc_version,
        model_setup_required: status.model_setup_required,
        active_model: status.active_model,
      });
      model().hydrateFromStatus(status);
      if (status.degraded) backend().setDegraded([]);
    })
    .catch((err: unknown) => {
      if (!(err instanceof IpcCallError)) throw err; // client already recorded store state
    });

  return () => {
    unsubs.forEach((u) => u());
    unlistenExit();
    unlistenError();
  };
}
