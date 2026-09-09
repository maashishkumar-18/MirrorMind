import { create } from "zustand";

/**
 * The single piece of Settings state that lives outside a panel: the last-export
 * timestamp, so the nav rail can show the proactive-export badge without every
 * panel re-fetching it. Source of truth is the backend's `AppConfig`
 * (`last_exported_at`), surfaced via the `app.status` probe in `bootstrap.ts`
 * and re-read after an export / wipe.
 */
export interface SettingsState {
  lastExportedAt: string | null;
  setLastExportedAt: (v: string | null) => void;
}

export const useSettingsStore = create<SettingsState>((set) => ({
  lastExportedAt: null,
  setLastExportedAt: (v) => set({ lastExportedAt: v }),
}));
