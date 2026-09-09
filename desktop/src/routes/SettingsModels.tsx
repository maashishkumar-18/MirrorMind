import { useCallback, useEffect, useState } from "react";

import { call, IpcCallError } from "../ipc/client";
import { useModelStore } from "../store/model";
import { ModelCatalogList } from "../ui/ModelCatalogList";

function msg(e: unknown): string {
  return e instanceof IpcCallError ? e.message : String(e);
}

/**
 * Settings → Models (Phase 3 Step 3.4). Reuses the fe.6 machinery — `useModelStore`
 * + `ModelCatalogList` + `model.catalog/status/download/activate`. Unlike
 * `/first-run` there is no "must activate to proceed" gate: an installed
 * non-active model shows "Switch", and switching does not navigate away.
 */
export function SettingsModels() {
  const activeModel = useModelStore((s) => s.activeModel);
  const [activating, setActivating] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    useModelStore.setState({ catalogError: null });
    const [cat, st] = await Promise.allSettled([call("model.catalog", {}), call("model.status", {})]);
    if (cat.status === "fulfilled") useModelStore.getState().setCatalog(cat.value.models);
    else useModelStore.getState().setCatalogError(msg(cat.reason));
    if (st.status === "fulfilled")
      useModelStore.getState().setStatuses(st.value.statuses, st.value.ollama_running);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const startDownload = (name: string) => {
    useModelStore.getState().startDownload(name);
    void call("model.download", { name }, { timeoutMs: 0 })
      .then((r) => {
        useModelStore.getState().finishDownload(name, r.verified);
        void load();
      })
      .catch((e) => useModelStore.getState().failDownload(name, msg(e)));
  };

  const activate = (name: string) => {
    setActivating(name);
    setError(null);
    void call("model.activate", { name })
      .then((r) => {
        useModelStore.getState().activate(r.active_model);
        useModelStore.getState().setStatus(name, "active");
        void load();
      })
      .catch((e) => setError(msg(e)))
      .finally(() => setActivating(null));
  };

  return (
    <section className="settings-panel">
      <h2>Models</h2>
      <p className="settings-hint">
        {activeModel ? `Active model: ${activeModel}` : "No model is active yet."}
      </p>

      <ModelCatalogList
        variant="settings"
        activatingName={activating}
        onDownload={startDownload}
        onActivate={activate}
        onRetry={() => void load()}
      />

      {error && (
        <p className="first-run-error">
          {error}{" "}
          <button type="button" onClick={() => setError(null)}>
            Dismiss
          </button>
        </p>
      )}
    </section>
  );
}
