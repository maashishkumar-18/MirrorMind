import { describe, expect, it } from "vitest";

import type { Todo } from "../store/todos";
import { priorityLabel, priorityRank, splitTodos } from "./todosView";

function todo(over: Partial<Todo> & { id: string }): Todo {
  return {
    session_id: null,
    title: over.id,
    notes: "",
    priority: null,
    category: null,
    completed_at: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...over,
  };
}

describe("priorityRank", () => {
  it("orders high < medium < low < none", () => {
    expect(priorityRank("high")).toBeLessThan(priorityRank("medium"));
    expect(priorityRank("medium")).toBeLessThan(priorityRank("low"));
    expect(priorityRank("low")).toBeLessThan(priorityRank(null));
    expect(priorityRank("bogus")).toBe(priorityRank(null));
  });
});

describe("priorityLabel", () => {
  it("capitalises or falls back", () => {
    expect(priorityLabel("high")).toBe("High");
    expect(priorityLabel(null)).toBe("No priority");
  });
});

describe("splitTodos", () => {
  it("separates active from completed", () => {
    const { active, completed } = splitTodos([
      todo({ id: "a" }),
      todo({ id: "b", completed_at: "2026-09-05T00:00:00Z" }),
    ]);
    expect(active.map((t) => t.id)).toEqual(["a"]);
    expect(completed.map((t) => t.id)).toEqual(["b"]);
  });

  it("sorts active by priority then age", () => {
    const { active } = splitTodos([
      todo({ id: "low", priority: "low", created_at: "2026-09-01T00:00:00Z" }),
      todo({ id: "high", priority: "high", created_at: "2026-09-03T00:00:00Z" }),
      todo({ id: "none-old", created_at: "2026-08-01T00:00:00Z" }),
      todo({ id: "none-new", created_at: "2026-09-09T00:00:00Z" }),
    ]);
    expect(active.map((t) => t.id)).toEqual(["high", "low", "none-old", "none-new"]);
  });

  it("sorts completed newest-completed first", () => {
    const { completed } = splitTodos([
      todo({ id: "old", completed_at: "2026-09-01T00:00:00Z" }),
      todo({ id: "new", completed_at: "2026-09-08T00:00:00Z" }),
    ]);
    expect(completed.map((t) => t.id)).toEqual(["new", "old"]);
  });
});
