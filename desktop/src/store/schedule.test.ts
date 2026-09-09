import { beforeEach, describe, expect, it } from "vitest";

import { useScheduleStore, type ScheduleItem } from "./schedule";

const initial = useScheduleStore.getState();
beforeEach(() => useScheduleStore.setState(initial, true));

function item(over: Partial<ScheduleItem> & { id: string }): ScheduleItem {
  return {
    schedule_id: "sch_1",
    title: over.id,
    start_time: "2026-09-08T09:00:00Z",
    end_time: "2026-09-08T10:00:00Z",
    location: "",
    notes: "",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...over,
  };
}

describe("useScheduleStore", () => {
  it("setDays populates + clears loading", () => {
    useScheduleStore.getState().startLoad();
    useScheduleStore.getState().setDays([{ date: "2026-09-08", items: [item({ id: "a" })] }]);
    expect(useScheduleStore.getState().days[0].items.map((i) => i.id)).toEqual(["a"]);
    expect(useScheduleStore.getState().loading).toBe(false);
  });

  it("patchItem replaces an item wherever it sits", () => {
    useScheduleStore.getState().setDays([
      { date: "2026-09-08", items: [item({ id: "a", title: "old" })] },
      { date: "2026-09-09", items: [item({ id: "b" })] },
    ]);
    useScheduleStore.getState().patchItem(item({ id: "a", title: "new" }));
    expect(useScheduleStore.getState().days[0].items[0].title).toBe("new");
  });

  it("removeItem drops it from every day", () => {
    useScheduleStore.getState().setDays([
      { date: "2026-09-08", items: [item({ id: "a" }), item({ id: "b" })] },
    ]);
    useScheduleStore.getState().removeItem("a");
    expect(useScheduleStore.getState().days[0].items.map((i) => i.id)).toEqual(["b"]);
  });

  it("view + anchor are settable", () => {
    useScheduleStore.getState().setView("week");
    useScheduleStore.getState().setAnchor("2026-09-07");
    expect(useScheduleStore.getState().view).toBe("week");
    expect(useScheduleStore.getState().anchor).toBe("2026-09-07");
  });
});
