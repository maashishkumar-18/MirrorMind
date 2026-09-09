/**
 * Pure helpers for the To-dos view (Phase 3 Step 3.3c). No React, no store.
 */
import type { Todo } from "../store/todos";

const PRIORITY_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };

export function priorityRank(priority: string | null): number {
  return priority != null && priority in PRIORITY_RANK ? PRIORITY_RANK[priority] : 3;
}

export function priorityLabel(priority: string | null): string {
  return priority ? priority[0].toUpperCase() + priority.slice(1) : "No priority";
}

/** Active first (by priority, then oldest first); completed sorted newest-completed first. */
export function splitTodos(todos: Todo[]): { active: Todo[]; completed: Todo[] } {
  const active = todos
    .filter((t) => t.completed_at == null)
    .sort(
      (a, b) =>
        priorityRank(a.priority) - priorityRank(b.priority) ||
        a.created_at.localeCompare(b.created_at),
    );
  const completed = todos
    .filter((t) => t.completed_at != null)
    .sort((a, b) => (b.completed_at ?? "").localeCompare(a.completed_at ?? ""));
  return { active, completed };
}
