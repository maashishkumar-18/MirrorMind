import type { AppReadyEvent, AppStatusResult, ModelDownloadProgressEvent } from "@ipc/methods";
import { create } from "zustand";
import type { z } from "zod";

type DownloadProgress = z.infer<typeof ModelDownloadProgressEvent>;

/**
 * Model state fe.4 can actually populate: the active model + first-launch gate
 * (from `app.ready` / `app.status`) and streaming download progress (from the
 * `model.download.progress` event). The bundled `catalog` and per-model
 * `statuses` land in fe.6, when the first-run flow calls `model.catalog` /
 * `model.status`.
 */
export interface ModelState {
  activeModel: string | null;
  modelSetupRequired: boolean;
  downloadProgress: DownloadProgress | null;

  hydrateFromReady: (payload: z.infer<typeof AppReadyEvent>) => void;
  hydrateFromStatus: (payload: z.infer<typeof AppStatusResult>) => void;
  setDownloadProgress: (payload: DownloadProgress) => void;
  clearDownloadProgress: () => void;
}

export const useModelStore = create<ModelState>((set) => ({
  activeModel: null,
  modelSetupRequired: true,
  downloadProgress: null,

  hydrateFromReady: (payload) =>
    set({ activeModel: payload.active_model, modelSetupRequired: payload.model_setup_required }),
  hydrateFromStatus: (payload) =>
    set({ activeModel: payload.active_model, modelSetupRequired: payload.model_setup_required }),
  setDownloadProgress: (payload) => set({ downloadProgress: payload }),
  clearDownloadProgress: () => set({ downloadProgress: null }),
}));
