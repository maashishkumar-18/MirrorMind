import { beforeEach, describe, expect, it } from "vitest";

import { useReminderStore, type Reminder } from "./reminders";

const initial = useReminderStore.getState();
beforeEach(() => useReminderStore.setState(initial, true));

function rem(over: Partial<Reminder> & { id: string }): Reminder {
  return {
    title: over.id,
    scheduled_time: "2026-09-10T09:00:00+00:00",
    notes: "",
    fired_at: null,
    completed_at: null,
    dismissed_at: null,
    created_at: "2026-09-01T00:00:00+00:00",
    ...over,
  };
}

describe("useReminderStore", () => {
  it("splits reconciliation into overdue + pending-ack", () => {
    useReminderStore.getState().setReconciliation({
      overdue: [rem({ id: "reminder_1" })],
      pending_acknowledgment: [],
    });
    expect(useReminderStore.getState().overdue).toHaveLength(1);
    expect(useReminderStore.getState().pendingAcknowledgment).toHaveLength(0);
  });

  it("setActive replaces the list and clears loading", () => {
    useReminderStore.getState().startLoad();
    useReminderStore.getState().setActive([rem({ id: "a" }), rem({ id: "b" })]);
    expect(useReminderStore.getState().active.map((r) => r.id)).toEqual(["a", "b"]);
    expect(useReminderStore.getState().loading).toBe(false);
  });

  it("patchReminder drops a now-completed reminder from active", () => {
    useReminderStore.getState().setActive([rem({ id: "a" }), rem({ id: "b" })]);
    useReminderStore
      .getState()
      .patchReminder(rem({ id: "a", completed_at: "2026-09-10T10:00:00+00:00" }));
    expect(useReminderStore.getState().active.map((r) => r.id)).toEqual(["b"]);
  });

  it("patchReminder replaces a still-active reminder in place", () => {
    useReminderStore.getState().setActive([rem({ id: "a", title: "old" })]);
    useReminderStore.getState().patchReminder(rem({ id: "a", title: "new" }));
    expect(useReminderStore.getState().active[0].title).toBe("new");
  });

  it("pruneReconciliation removes an id from both overlays", () => {
    useReminderStore.getState().setReconciliation({
      overdue: [rem({ id: "a" }), rem({ id: "b" })],
      pending_acknowledgment: [rem({ id: "a" })],
    });
    useReminderStore.getState().pruneReconciliation("a");
    expect(useReminderStore.getState().overdue.map((r) => r.id)).toEqual(["b"]);
    expect(useReminderStore.getState().pendingAcknowledgment).toEqual([]);
  });

  it("removeReminder drops it from active", () => {
    useReminderStore.getState().setActive([rem({ id: "a" }), rem({ id: "b" })]);
    useReminderStore.getState().removeReminder("a");
    expect(useReminderStore.getState().active.map((r) => r.id)).toEqual(["b"]);
  });
});
