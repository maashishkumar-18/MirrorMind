import { useCallback, useEffect, useReducer } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { call, IpcCallError } from "../ipc/client";
import { useBackendStore } from "../store/backend";
import { useModelStore } from "../store/model";
import { ModelCatalogList } from "../ui/ModelCatalogList";
import { Starting } from "../ui/Starting";
import { firstRunInitial, firstRunReducer } from "./firstRunReducer";
import { S } from "../strings";

function msg(e: unknown): string {
  return e instanceof IpcCallError ? e.message : String(e);
}

export function FirstRun() {
  const phase = useBackendStore((s) => s.phase);
  const navigate = useNavigate();

  const modelSetupRequired = useModelStore((s) => s.modelSetupRequired);

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
        <h1>{S.firstRun.recoveryTitle}</h1>
        <p>{S.firstRun.recoveryBody}</p>
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

  return (
    <div className="screen">
      <h1>{S.firstRun.chooseTitle}</h1>
      <p>{S.firstRun.chooseBody}</p>

      <ModelCatalogList
        variant="first-run"
        activatingName={s.activating}
        onDownload={startDownload}
        onActivate={activate}
        onRetry={() => void load()}
      />

      {s.activateError && (
        <p className="first-run-error">
          {s.activateError}{" "}
          <button type="button" onClick={() => dispatch({ type: "activate/reset" })}>
            {S.loading.dismiss}
          </button>
        </p>
      )}
    </div>
  );
}
