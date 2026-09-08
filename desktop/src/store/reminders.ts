import type { AppRemindersPendingEvent, ReminderWire } from "@ipc/methods";
import { create } from "zustand";
import type { z } from "zod";

type Reminder = z.infer<typeof ReminderWire>;

/**
 * On-launch reminder reconciliation (project_logic §9). Populated from the
 * `app.reminders_pending` event; the Reminders view (Step 3.3) renders it.
 */
export interface ReminderState {
  overdue: Reminder[];
  pendingAcknowledgment: Reminder[];
  setReconciliation: (payload: z.infer<typeof AppRemindersPendingEvent>) => void;
}

export const useReminderStore = create<ReminderState>((set) => ({
  overdue: [],
  pendingAcknowledgment: [],
  setReconciliation: (payload) =>
    set({ overdue: payload.overdue, pendingAcknowledgment: payload.pending_acknowledgment }),
}));
