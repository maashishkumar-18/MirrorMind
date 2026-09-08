import type {
  AppReadyEvent,
  AppStatusResult,
  CatalogEntry,
  ModelDownloadProgressEvent,
} from "@ipc/methods";
import { create } from "zustand";
import type { z } from "zod";

type DownloadProgress = z.infer<typeof ModelDownloadProgressEvent>;
type Catalog = z.infer<typeof CatalogEntry>[];

/** name → "not_installed" | "downloading" | "available" | "active" */
export type ModelStatusMap = Record<string, string>;

/**
 * Model state: the active model + first-launch gate (from `app.ready` /
 * `app.status`), the bundled catalog + per-model statuses (fe.6's `/first-run`
 * flow), and the single-download lifecycle (kept here, not in the component, so
 * it survives navigating away from `/first-run` and back).
 */
export interface ModelState {
  activeModel: string | null;
  modelSetupRequired: boolean;

  catalog: Catalog | null;
  catalogError: string | null;
  statuses: ModelStatusMap;
  ollamaRunning: boolean;

  downloadingModel: string | null;
  downloadProgress: DownloadProgress | null;
  downloadError: string | null;
  downloadOutcome: { name: string; verified: boolean } | null;

  hydrateFromReady: (payload: z.infer<typeof AppReadyEvent>) => void;
  hydrateFromStatus: (payload: z.infer<typeof AppStatusResult>) => void;

  setCatalog: (entries: Catalog) => void;
  setCatalogError: (message: string) => void;
  setStatuses: (statuses: ModelStatusMap, ollamaRunning: boolean) => void;
  setStatus: (name: string, status: string) => void;

  setDownloadProgress: (payload: DownloadProgress) => void;
  startDownload: (name: string) => void;
  finishDownload: (name: string, verified: boolean) => void;
  failDownload: (name: string, message: string) => void;
  clearDownloadFeedback: () => void;

  activate: (name: string) => void;
}

export const useModelStore = create<ModelState>((set) => ({
  activeModel: null,
  modelSetupRequired: true,

  catalog: null,
  catalogError: null,
  statuses: {},
  ollamaRunning: false,

  downloadingModel: null,
  downloadProgress: null,
  downloadError: null,
  downloadOutcome: null,

  hydrateFromReady: (payload) =>
    set({ activeModel: payload.active_model, modelSetupRequired: payload.model_setup_required }),
  hydrateFromStatus: (payload) =>
    set({ activeModel: payload.active_model, modelSetupRequired: payload.model_setup_required }),

  setCatalog: (entries) => set({ catalog: entries, catalogError: null }),
  setCatalogError: (message) => set({ catalogError: message }),
  setStatuses: (statuses, ollamaRunning) => set({ statuses, ollamaRunning }),
  setStatus: (name, status) =>
    set((s) => ({ statuses: { ...s.statuses, [name]: status } })),

  setDownloadProgress: (payload) => set({ downloadProgress: payload }),
  startDownload: (name) =>
    set({
      downloadingModel: name,
      downloadError: null,
      downloadOutcome: null,
      downloadProgress: null,
    }),
  finishDownload: (name, verified) =>
    set((s) => ({
      downloadingModel: null,
      downloadProgress: null,
      downloadOutcome: { name, verified },
      statuses: { ...s.statuses, [name]: "available" },
    })),
  failDownload: (name, message) =>
    set((s) => ({
      downloadingModel: s.downloadingModel === name ? null : s.downloadingModel,
      downloadProgress: null,
      downloadError: message,
    })),
  clearDownloadFeedback: () => set({ downloadError: null, downloadOutcome: null }),

  activate: (name) => set({ activeModel: name, modelSetupRequired: false }),
}));
