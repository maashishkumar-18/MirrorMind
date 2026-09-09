import { beforeEach, describe, expect, it } from "vitest";

import { useTodoStore, type Todo } from "./todos";

const initial = useTodoStore.getState();
beforeEach(() => useTodoStore.setState(initial, true));

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

describe("useTodoStore", () => {
  it("setTodos replaces the list and clears loading", () => {
    useTodoStore.getState().startLoad();
    useTodoStore.getState().setTodos([todo({ id: "a" })]);
    expect(useTodoStore.getState().todos.map((t) => t.id)).toEqual(["a"]);
    expect(useTodoStore.getState().loading).toBe(false);
  });

  it("patchTodo replaces in place (completed todos are retained)", () => {
    useTodoStore.getState().setTodos([todo({ id: "a" }), todo({ id: "b" })]);
    useTodoStore.getState().patchTodo(todo({ id: "a", completed_at: "2026-09-05T00:00:00Z" }));
    expect(useTodoStore.getState().todos.map((t) => t.id)).toEqual(["a", "b"]);
    expect(useTodoStore.getState().todos[0].completed_at).not.toBeNull();
  });

  it("removeTodo drops it", () => {
    useTodoStore.getState().setTodos([todo({ id: "a" }), todo({ id: "b" })]);
    useTodoStore.getState().removeTodo("a");
    expect(useTodoStore.getState().todos.map((t) => t.id)).toEqual(["b"]);
  });

  it("failLoad records the error", () => {
    useTodoStore.getState().failLoad("boom");
    expect(useTodoStore.getState().error).toBe("boom");
    expect(useTodoStore.getState().loading).toBe(false);
  });
});
