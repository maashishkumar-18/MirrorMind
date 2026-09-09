import { describe, expect, it } from "vitest";

import type { Reminder } from "../store/reminders";
import { datetimeLocalToIso, groupReminders, isoToDatetimeLocal } from "./remindersView";

function rem(over: Partial<Reminder> & { id: string; scheduled_time: string }): Reminder {
  return {
    title: over.id,
    notes: "",
    fired_at: null,
    completed_at: null,
    dismissed_at: null,
    created_at: "2026-09-01T00:00:00Z",
    ...over,
  };
}

const NOW = "2026-09-10T12:00:00Z";

describe("groupReminders", () => {
  it("puts past-due reminders under overdue", () => {
    const g = groupReminders([rem({ id: "a", scheduled_time: "2026-09-09T09:00:00Z" })], new Set(), NOW);
    expect(g.overdue.map((r) => r.id)).toEqual(["a"]);
    expect(g.today).toEqual([]);
    expect(g.upcoming).toEqual([]);
  });

  it("honours the reconciliation overlay even for a future time", () => {
    const g = groupReminders(
      [rem({ id: "a", scheduled_time: "2026-09-20T09:00:00Z" })],
      new Set(["a"]),
      NOW,
    );
    expect(g.overdue.map((r) => r.id)).toEqual(["a"]);
  });

  it("does not double-list a reminder that is both in `active` and the overlay", () => {
    const r = rem({ id: "a", scheduled_time: "2026-09-09T09:00:00Z" });
    const g = groupReminders([r], new Set(["a"]), NOW);
    const all = [...g.overdue, ...g.today, ...g.upcoming];
    expect(all.filter((x) => x.id === "a")).toHaveLength(1);
  });

  it("splits today vs upcoming and sorts by time", () => {
    const g = groupReminders(
      [
        rem({ id: "later-today", scheduled_time: "2026-09-10T18:00:00Z" }),
        rem({ id: "next-week", scheduled_time: "2026-09-18T09:00:00Z" }),
        rem({ id: "tomorrow", scheduled_time: "2026-09-11T09:00:00Z" }),
      ],
      new Set(),
      NOW,
    );
    expect(g.today.map((r) => r.id)).toEqual(["later-today"]);
    expect(g.upcoming.map((r) => r.id)).toEqual(["tomorrow", "next-week"]);
  });
});

describe("datetime-local <-> ISO", () => {
  it("round-trips a value through local time", () => {
    const iso = "2026-09-10T15:30:00.000Z";
    const local = isoToDatetimeLocal(iso);
    expect(local).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
    expect(datetimeLocalToIso(local)).toBe(iso.replace(":00.000Z", ":00.000Z"));
  });

  it("returns null for an empty / unparseable value", () => {
    expect(datetimeLocalToIso("")).toBeNull();
    expect(datetimeLocalToIso("not-a-date")).toBeNull();
  });
});
