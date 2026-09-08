import { beforeEach, describe, expect, it } from "vitest";

import { useReminderStore } from "./reminders";

const initial = useReminderStore.getState();
beforeEach(() => useReminderStore.setState(initial, true));

describe("useReminderStore", () => {
  it("splits reconciliation into overdue + pending-ack", () => {
    const r = {
      id: "reminder_1",
      title: "Water the plants",
      scheduled_time: "2026-09-01T08:00:00+00:00",
      notes: "",
      fired_at: null,
      completed_at: null,
      dismissed_at: null,
      created_at: "2026-08-31T20:00:00+00:00",
    };
    useReminderStore.getState().setReconciliation({ overdue: [r], pending_acknowledgment: [] });
    expect(useReminderStore.getState().overdue).toHaveLength(1);
    expect(useReminderStore.getState().pendingAcknowledgment).toHaveLength(0);
  });
});
