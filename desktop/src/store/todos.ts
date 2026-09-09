import type { TodoWire } from "@ipc/methods";
import { create } from "zustand";
import type { z } from "zod";

export type Todo = z.infer<typeof TodoWire>;

/**
 * To-dos state (Phase 3 Step 3.3c). `todos` holds every non-deleted row
 * (active + completed); the view splits them into the list and the
 * "Completed" tab. Mutations are applied only on a successful IPC call.
 */
export interface TodoState {
  todos: Todo[];
  loading: boolean;
  error: string | null;

  startLoad: () => void;
  setTodos: (todos: Todo[]) => void;
  failLoad: (message: string) => void;
  patchTodo: (t: Todo) => void;
  removeTodo: (id: string) => void;
  clearError: () => void;
}

export const useTodoStore = create<TodoState>((set) => ({
  todos: [],
  loading: false,
  error: null,

  startLoad: () => set({ loading: true, error: null }),
  setTodos: (todos) => set({ todos, loading: false, error: null }),
  failLoad: (message) => set({ loading: false, error: message }),
  patchTodo: (t) =>
    set((s) => ({ todos: s.todos.map((x) => (x.id === t.id ? t : x)) })),
  removeTodo: (id) => set((s) => ({ todos: s.todos.filter((x) => x.id !== id) })),
  clearError: () => set({ error: null }),
}));
