import { useCallback, useEffect, useState } from "react";

import { call, IpcCallError } from "../ipc/client";
import { useTodoStore, type Todo } from "../store/todos";
import { actionLabel, interpretCreateResult, type CreateOutcome } from "./featureCreate";
import { priorityLabel, priorityRank, splitTodos } from "./todosView";
import { S } from "../strings";

function errText(e: unknown): string {
  return e instanceof IpcCallError ? e.message : String(e);
}

const PRIORITIES = ["high", "medium", "low"] as const;

export function Todos() {
  const todos = useTodoStore((s) => s.todos);
  const loading = useTodoStore((s) => s.loading);
  const error = useTodoStore((s) => s.error);

  const [tab, setTab] = useState<"active" | "completed">("active");
  const [draft, setDraft] = useState("");
  const [creating, setCreating] = useState(false);
  const [outcome, setOutcome] = useState<CreateOutcome | null>(null);

  const refresh = useCallback(() => {
    useTodoStore.getState().startLoad();
    void call("todos.list", {})
      .then((r) => useTodoStore.getState().setTodos(r.todos))
      .catch((e: unknown) => {
        useTodoStore.getState().failLoad(errText(e));
        if (!(e instanceof IpcCallError)) throw e;
      });
  }, []);

  useEffect(refresh, [refresh]);

  const { active, completed } = splitTodos(todos);
  const shown = tab === "active" ? active : completed;

  const complete = (t: Todo) =>
    void call("todos.complete", { id: t.id })
      .then((r) => useTodoStore.getState().patchTodo(r.todo))
      .catch(() => {});

  const remove = (t: Todo) =>
    void call("todos.delete", { id: t.id })
      .then(() => useTodoStore.getState().removeTodo(t.id))
      .catch(() => {});

  const setPriority = (t: Todo, priority: string) =>
    void call("todos.update", { id: t.id, priority: priority || null })
      .then((r) => useTodoStore.getState().patchTodo(r.todo))
      .catch(() => {});

  const rename = (t: Todo, title: string) => {
    if (!title.trim() || title === t.title) return;
    void call("todos.update", { id: t.id, title })
      .then((r) => useTodoStore.getState().patchTodo(r.todo))
      .catch(() => {});
  };

  const submitCreate = (e: React.FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || creating) return;
    setCreating(true);
    setOutcome(null);
    void call("chat.send", { text })
      .then((r) => {
        const o = interpretCreateResult(r);
        setOutcome(o);
        if (o.kind === "created") {
          setDraft("");
          refresh();
        }
      })
      .catch((e) => setOutcome({ kind: "message", text: errText(e) }))
      .finally(() => setCreating(false));
  };

  const resolveDisambiguation = (pendingActionId: string, choice: string) => {
    setCreating(true);
    void call("chat.confirm_action", { pending_action_id: pendingActionId, choice })
      .then((r) => {
        const o = interpretCreateResult(r);
        setOutcome(o.kind === "created" ? null : o);
        if (o.kind === "created") {
          setDraft("");
          refresh();
        }
      })
      .catch((e) => setOutcome({ kind: "message", text: errText(e) }))
      .finally(() => setCreating(false));
  };

  return (
    <div className="feature-view">
      <header className="feature-header">
        <h1>{S.features.todos.title}</h1>
      </header>

      <form className="feature-create" onSubmit={submitCreate}>
        <input
          aria-label={S.features.todos.createLabel}
          placeholder={S.features.todos.createPlaceholder}
          value={draft}
          disabled={creating}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button type="submit" disabled={creating || !draft.trim()}>
          Add
        </button>
      </form>

      {outcome && outcome.kind !== "created" && (
        <div className="feature-create-fallback" role="status">
          {outcome.kind === "disambiguation" ? (
            <>
              <p>{S.features.didYouMean}</p>
              <div className="feature-choice-row">
                {outcome.options.map((opt) => (
                  <button
                    key={opt}
                    type="button"
                    disabled={creating}
                    onClick={() => resolveDisambiguation(outcome.pendingActionId, opt)}
                  >
                    {actionLabel(opt)}
                  </button>
                ))}
              </div>
            </>
          ) : outcome.kind === "conflict" ? (
            <p>{outcome.answer} {S.features.conflictHint}</p>
          ) : (
            <p>{outcome.text}</p>
          )}
          <button type="button" className="feature-dismiss" onClick={() => setOutcome(null)}>
            {S.features.dismiss}
          </button>
        </div>
      )}

      <div className="todo-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "active"}
          onClick={() => setTab("active")}
        >
          Active ({active.length})
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "completed"}
          onClick={() => setTab("completed")}
        >
          {S.features.todos.completed(completed.length)}
        </button>
      </div>

      {error && <p className="feature-error">Couldn&apos;t load to-dos: {error}</p>}
      {loading && todos.length === 0 && <p className="feature-loading">{S.features.loading}</p>}
      {!loading && shown.length === 0 && !error && (
        <p className="feature-empty">{S.features.empty}</p>
      )}

      <ul className="feature-list">
        {shown.map((t) => (
          <li key={t.id} className="feature-row" data-priority={t.priority ?? undefined}>
            <input
              type="checkbox"
              aria-label={S.features.complete(t.title)}
              checked={t.completed_at != null}
              disabled={t.completed_at != null}
              onChange={() => complete(t)}
            />
            <div className="feature-row-body">
              <input
                className="feature-row-title-edit"
                aria-label={`Edit ${t.title}`}
                defaultValue={t.title}
                disabled={t.completed_at != null}
                onBlur={(e) => rename(t, e.target.value)}
              />
              <span className="feature-row-meta">
                <span className="todo-priority" data-level={priorityRank(t.priority)}>
                  {priorityLabel(t.priority)}
                </span>
                {t.category && <span className="todo-category">{t.category}</span>}
              </span>
              {t.notes && <span className="feature-row-notes">{t.notes}</span>}
            </div>
            {t.completed_at == null && (
              <>
                <label className="feature-row-reschedule">
                  <span className="visually-hidden">Priority for {t.title}</span>
                  <select
                    value={t.priority ?? ""}
                    onChange={(e) => setPriority(t, e.target.value)}
                  >
                    <option value="">{S.features.todos.noPriority}</option>
                    {PRIORITIES.map((p) => (
                      <option key={p} value={p}>
                        {priorityLabel(p)}
                      </option>
                    ))}
                  </select>
                </label>
                <button type="button" className="feature-delete" onClick={() => remove(t)}>
                  Delete
                </button>
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
