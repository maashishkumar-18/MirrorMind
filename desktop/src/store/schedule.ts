import type { ScheduleItemWire } from "@ipc/methods";
import { create } from "zustand";
import type { z } from "zod";

export type ScheduleItem = z.infer<typeof ScheduleItemWire>;

export interface ScheduleDay {
  date: string;
  items: ScheduleItem[];
}

/**
 * Schedule state (Phase 3 Step 3.3e). `days` holds one entry for the day view
 * and seven for the week view. `anchor` is the day-view date / week start
 * (`YYYY-MM-DD`).
 */
export interface ScheduleState {
  view: "day" | "week";
  anchor: string;
  days: ScheduleDay[];
  loading: boolean;
  error: string | null;

  setView: (view: "day" | "week") => void;
  setAnchor: (anchor: string) => void;
  startLoad: () => void;
  setDays: (days: ScheduleDay[]) => void;
  failLoad: (message: string) => void;
  patchItem: (item: ScheduleItem) => void;
  removeItem: (id: string) => void;
  clearError: () => void;
}

const todayKey = () => new Date().toISOString().slice(0, 10);

export const useScheduleStore = create<ScheduleState>((set) => ({
  view: "day",
  anchor: todayKey(),
  days: [],
  loading: false,
  error: null,

  setView: (view) => set({ view }),
  setAnchor: (anchor) => set({ anchor }),
  startLoad: () => set({ loading: true, error: null }),
  setDays: (days) => set({ days, loading: false, error: null }),
  failLoad: (message) => set({ loading: false, error: message }),
  patchItem: (item) =>
    set((s) => ({
      days: s.days.map((d) => ({
        ...d,
        items: d.items.map((x) => (x.id === item.id ? item : x)),
      })),
    })),
  removeItem: (id) =>
    set((s) => ({
      days: s.days.map((d) => ({ ...d, items: d.items.filter((x) => x.id !== id) })),
    })),
  clearError: () => set({ error: null }),
}));
