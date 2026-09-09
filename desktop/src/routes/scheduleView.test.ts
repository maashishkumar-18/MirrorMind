import { describe, expect, it } from "vitest";

import type { ScheduleItem } from "../store/schedule";
import { addDays, layoutDay, overwriteParams, weekStart } from "./scheduleView";

function item(over: Partial<ScheduleItem> & { id: string; start_time: string; end_time: string }): ScheduleItem {
  return {
    schedule_id: "sch_1",
    title: over.id,
    location: "",
    notes: "",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...over,
  };
}

describe("weekStart", () => {
  it("returns the Monday on or before the date", () => {
    expect(weekStart("2026-09-10")).toBe("2026-09-07"); // Thu -> Mon
    expect(weekStart("2026-09-07")).toBe("2026-09-07"); // Mon -> itself
    expect(weekStart("2026-09-13")).toBe("2026-09-07"); // Sun -> prior Mon
  });
});

describe("addDays", () => {
  it("moves forward and backward across month boundaries", () => {
    expect(addDays("2026-09-30", 1)).toBe("2026-10-01");
    expect(addDays("2026-09-01", -1)).toBe("2026-08-31");
  });
});

describe("layoutDay", () => {
  it("orders by start time and flags overlapping items", () => {
    const slots = layoutDay([
      item({ id: "b", start_time: "2026-09-08T10:00:00Z", end_time: "2026-09-08T11:00:00Z" }),
      item({ id: "a", start_time: "2026-09-08T09:00:00Z", end_time: "2026-09-08T09:30:00Z" }),
      item({ id: "c", start_time: "2026-09-08T10:30:00Z", end_time: "2026-09-08T11:30:00Z" }),
    ]);
    expect(slots.map((s) => s.item.id)).toEqual(["a", "b", "c"]);
    expect(slots.map((s) => s.overlaps)).toEqual([false, true, true]);
  });
});

describe("overwriteParams", () => {
  it("rebuilds a create call from the conflict's `attempted` + every conflicting id", () => {
    const conflict = {
      attempted: {
        title: "Dentist",
        start_time: "2026-09-08T09:00:00Z",
        end_time: "2026-09-08T10:00:00Z",
        location: "Downtown",
      },
      conflicts_with: [
        { id: "sci_1", title: "Standup", start_time: "x", end_time: "y" },
        { id: "sci_2", title: "Sync", start_time: "x", end_time: "y" },
      ],
    };
    expect(overwriteParams(conflict)).toEqual({
      title: "Dentist",
      start_time: "2026-09-08T09:00:00Z",
      end_time: "2026-09-08T10:00:00Z",
      location: "Downtown",
      overwrite_ids: ["sci_1", "sci_2"],
    });
  });
});
