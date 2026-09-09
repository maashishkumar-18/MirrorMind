import { useCallback, useEffect, useState } from "react";

import { call, IpcCallError } from "../ipc/client";
import { useReminderStore, type Reminder } from "../store/reminders";
import {
  actionLabel,
  interpretCreateResult,
  type CreateOutcome,
} from "./featureCreate";
import {
  datetimeLocalToIso,
  formatWhen,
  groupReminders,
  isoToDatetimeLocal,
} from "./remindersView";

function errText(e: unknown): string {
  return e instanceof IpcCallError ? e.message : String(e);
}

export function Reminders() {
  const active = useReminderStore((s) => s.active);
  const overdue = useReminderStore((s) => s.overdue);
  const pendingAck = useReminderStore((s) => s.pendingAcknowledgment);
  const loading = useReminderStore((s) => s.loading);
  const error = useReminderStore((s) => s.error);

  const [draft, setDraft] = useState("");
  const [creating, setCreating] = useState(false);
  const [outcome, setOutcome] = useState<CreateOutcome | null>(null);

  const refresh = useCallback(() => {
    useReminderStore.getState().startLoad();
    void call("reminders.list", {})
      .then((r) => useReminderStore.getState().setActive(r.reminders))
      .catch((e: unknown) => {
        useReminderStore.getState().failLoad(errText(e));
        if (!(e instanceof IpcCallError)) throw e;
      });
  }, []);

  useEffect(refresh, [refresh]);

  const groups = groupReminders(
    active,
    new Set(overdue.map((r) => r.id)),
    new Date().toISOString(),
  );
  const pendingIds = new Set(pendingAck.map((r) => r.id));

  const complete = (r: Reminder) =>
    void call("reminders.complete", { id: r.id })
      .then((res) => {
        useReminderStore.getState().patchReminder(res.reminder);
        useReminderStore.getState().pruneReconciliation(r.id);
      })
      .catch(() => {});

  const remove = (r: Reminder) =>
    void call("reminders.delete", { id: r.id })
      .then(() => {
        useReminderStore.getState().removeReminder(r.id);
        useReminderStore.getState().pruneReconciliation(r.id);
      })
      .catch(() => {});

  const reschedule = (r: Reminder, localValue: string) => {
    const iso = datetimeLocalToIso(localValue);
    if (!iso) return;
    void call("reminders.reschedule", { id: r.id, scheduled_time: iso })
      .then((res) => {
        useReminderStore.getState().patchReminder(res.reminder);
        useReminderStore.getState().pruneReconciliation(r.id);
      })
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
        <h1>Reminders</h1>
      </header>

      <form className="feature-create" onSubmit={submitCreate}>
        <input
          aria-label="New reminder"
          placeholder="Remind me to call John on Tuesday at 2pm"
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
              <p>Did you mean:</p>
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
            <p>{outcome.answer} — open the Schedule view to resolve it.</p>
          ) : (
            <p>{outcome.text}</p>
          )}
          <button type="button" className="feature-dismiss" onClick={() => setOutcome(null)}>
            Dismiss
          </button>
        </div>
      )}

      {error && <p className="feature-error">Couldn&apos;t load reminders: {error}</p>}
      {loading && active.length === 0 && <p className="feature-loading">Loading…</p>}
      {!loading && active.length === 0 && !error && (
        <p className="feature-empty">Nothing here yet.</p>
      )}

      <Group title="Overdue" reminders={groups.overdue} overdue pendingIds={pendingIds}
        onComplete={complete} onDelete={remove} onReschedule={reschedule} />
      <Group title="Today" reminders={groups.today} pendingIds={pendingIds}
        onComplete={complete} onDelete={remove} onReschedule={reschedule} />
      <Group title="Upcoming" reminders={groups.upcoming} pendingIds={pendingIds}
        onComplete={complete} onDelete={remove} onReschedule={reschedule} />
    </div>
  );
}

function Group({
  title,
  reminders,
  overdue = false,
  pendingIds,
  onComplete,
  onDelete,
  onReschedule,
}: {
  title: string;
  reminders: Reminder[];
  overdue?: boolean;
  pendingIds: Set<string>;
  onComplete: (r: Reminder) => void;
  onDelete: (r: Reminder) => void;
  onReschedule: (r: Reminder, localValue: string) => void;
}) {
  if (reminders.length === 0) return null;
  return (
    <section className="feature-group">
      <h2>{title}</h2>
      <ul className="feature-list">
        {reminders.map((r) => (
          <li
            key={r.id}
            className={`feature-row${overdue ? " reminder-overdue" : ""}`}
            data-overdue={overdue || undefined}
          >
            <input
              type="checkbox"
              aria-label={`Complete ${r.title}`}
              checked={false}
              onChange={() => onComplete(r)}
            />
            <div className="feature-row-body">
              <span className="feature-row-title">{r.title}</span>
              <span className="feature-row-meta">
                {formatWhen(r.scheduled_time)}
                {pendingIds.has(r.id) && <em className="reminder-pending"> · needs acknowledgment</em>}
              </span>
              {r.notes && <span className="feature-row-notes">{r.notes}</span>}
            </div>
            <label className="feature-row-reschedule">
              <span className="visually-hidden">Reschedule {r.title}</span>
              <input
                type="datetime-local"
                defaultValue={isoToDatetimeLocal(r.scheduled_time)}
                onChange={(e) => onReschedule(r, e.target.value)}
              />
            </label>
            <button type="button" className="feature-delete" onClick={() => onDelete(r)}>
              Delete
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
