import type { AppRemindersPendingEvent, ReminderWire } from "@ipc/methods";
import { create } from "zustand";
import type { z } from "zod";

export type Reminder = z.infer<typeof ReminderWire>;

/**
 * Reminders state (Phase 3 Step 3.3 — extends the fe.4 reconciliation store).
 *
 * `active` is the authoritative list rendered by the Reminders view (from
 * `reminders.list`). `overdue` / `pendingAcknowledgment` come from the
 * on-launch `app.reminders_pending` event and are used only as **id-set
 * overlays** — they never render as separate rows (a fired-but-unacked reminder
 * is already in `active`). Lifecycle actions prune both an `active` entry and
 * the overlays (Q3 — the local half; the worker prunes its own cache too).
 */
export interface ReminderState {
  active: Reminder[];
  overdue: Reminder[];
  pendingAcknowledgment: Reminder[];
  loading: boolean;
  error: string | null;

  setReconciliation: (payload: z.infer<typeof AppRemindersPendingEvent>) => void;
  startLoad: () => void;
  setActive: (reminders: Reminder[]) => void;
  failLoad: (message: string) => void;
  /** Replace one reminder; drop it from `active` if it is now completed/dismissed. */
  patchReminder: (r: Reminder) => void;
  removeReminder: (id: string) => void;
  /** Drop an id from the reconciliation overlays (Q3). */
  pruneReconciliation: (id: string) => void;
  clearError: () => void;
}

const isActive = (r: Reminder) => r.completed_at == null && r.dismissed_at == null;

export const useReminderStore = create<ReminderState>((set) => ({
  active: [],
  overdue: [],
  pendingAcknowledgment: [],
  loading: false,
  error: null,

  setReconciliation: (payload) =>
    set({ overdue: payload.overdue, pendingAcknowledgment: payload.pending_acknowledgment }),

  startLoad: () => set({ loading: true, error: null }),
  setActive: (reminders) => set({ active: reminders, loading: false, error: null }),
  failLoad: (message) => set({ loading: false, error: message }),

  patchReminder: (r) =>
    set((s) => {
      const rest = s.active.filter((x) => x.id !== r.id);
      return { active: isActive(r) ? [...rest, r] : rest };
    }),
  removeReminder: (id) => set((s) => ({ active: s.active.filter((x) => x.id !== id) })),

  pruneReconciliation: (id) =>
    set((s) => ({
      overdue: s.overdue.filter((x) => x.id !== id),
      pendingAcknowledgment: s.pendingAcknowledgment.filter((x) => x.id !== id),
    })),

  clearError: () => set({ error: null }),
}));
