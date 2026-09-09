import { useCallback, useEffect, useState } from "react";

import { call, IpcCallError } from "../ipc/client";
import { useScheduleStore, type ScheduleItem } from "../store/schedule";
import { useSessionStore } from "../store/session";
import type { ChatConflict } from "./featureCreate";
import { actionLabel, interpretCreateResult, type CreateOutcome } from "./featureCreate";
import {
  addDays,
  formatDayLabel,
  layoutDay,
  overwriteParams,
  timeLabel,
  weekStart,
} from "./scheduleView";

function errText(e: unknown): string {
  return e instanceof IpcCallError ? e.message : String(e);
}

export function Schedule() {
  const view = useScheduleStore((s) => s.view);
  const anchor = useScheduleStore((s) => s.anchor);
  const days = useScheduleStore((s) => s.days);
  const loading = useScheduleStore((s) => s.loading);
  const error = useScheduleStore((s) => s.error);

  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<CreateOutcome | null>(null);
  const [conflict, setConflict] = useState<ChatConflict | null>(null);

  // A conflict raised from the chat view lands here — the only resolution surface.
  useEffect(() => {
    const pending = useSessionStore.getState().pendingConflict;
    if (pending) setConflict(pending as ChatConflict);
  }, []);

  const refresh = useCallback(() => {
    useScheduleStore.getState().startLoad();
    const s = useScheduleStore.getState();
    const p =
      s.view === "day"
        ? call("schedule.day", { date: s.anchor }).then((r) => [
            { date: r.date, items: r.items },
          ])
        : call("schedule.week", { start_date: weekStart(s.anchor) }).then((r) =>
            r.days.map((d) => ({ date: d.date, items: d.items })),
          );
    void p
      .then((d) => useScheduleStore.getState().setDays(d))
      .catch((e: unknown) => {
        useScheduleStore.getState().failLoad(errText(e));
        if (!(e instanceof IpcCallError)) throw e;
      });
  }, []);

  useEffect(refresh, [refresh, view, anchor]);

  const step = (dir: -1 | 1) =>
    useScheduleStore.getState().setAnchor(addDays(anchor, dir * (view === "day" ? 1 : 7)));

  const clearConflict = () => {
    setConflict(null);
    useSessionStore.setState({ pendingConflict: null });
  };

  const overwrite = () => {
    if (!conflict) return;
    setBusy(true);
    void call("schedule.create_item", overwriteParams(conflict))
      .then(() => {
        clearConflict();
        setOutcome(null);
        refresh();
      })
      .catch((e: unknown) => setOutcome({ kind: "message", text: errText(e) }))
      .finally(() => setBusy(false));
  };

  const submitEdit = (e: React.FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setBusy(true);
    setOutcome(null);
    void call("chat.send", { text })
      .then((r) => {
        const o = interpretCreateResult(r);
        if (o.kind === "conflict") {
          setConflict(o.conflict);
          setOutcome(null);
        } else {
          setOutcome(o.kind === "created" ? null : o);
          if (o.kind === "created") {
            setDraft("");
            refresh();
          }
        }
      })
      .catch((e) => setOutcome({ kind: "message", text: errText(e) }))
      .finally(() => setBusy(false));
  };

  const resolveDisambiguation = (pendingActionId: string, choice: string) => {
    setBusy(true);
    void call("chat.confirm_action", { pending_action_id: pendingActionId, choice })
      .then((r) => {
        const o = interpretCreateResult(r);
        if (o.kind === "conflict") setConflict(o.conflict);
        else {
          setOutcome(o.kind === "created" ? null : o);
          if (o.kind === "created") {
            setDraft("");
            refresh();
          }
        }
      })
      .catch((e) => setOutcome({ kind: "message", text: errText(e) }))
      .finally(() => setBusy(false));
  };

  const removeItem = (item: ScheduleItem) =>
    void call("schedule.delete", { id: item.id })
      .then(() => useScheduleStore.getState().removeItem(item.id))
      .catch(() => {});

  return (
    <div className="feature-view">
      <header className="feature-header">
        <h1>Schedule</h1>
        <div className="schedule-toggle" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={view === "day"}
            onClick={() => useScheduleStore.getState().setView("day")}
          >
            Day
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "week"}
            onClick={() => useScheduleStore.getState().setView("week")}
          >
            Week
          </button>
        </div>
      </header>

      <div className="schedule-nav">
        <button type="button" onClick={() => step(-1)} aria-label="Previous">
          ‹
        </button>
        <span>
          {view === "day"
            ? formatDayLabel(anchor)
            : `Week of ${formatDayLabel(weekStart(anchor))}`}
        </span>
        <button type="button" onClick={() => step(1)} aria-label="Next">
          ›
        </button>
        <button type="button" onClick={() => useScheduleStore.getState().setAnchor(new Date().toISOString().slice(0, 10))}>
          Today
        </button>
      </div>

      <form className="feature-create" onSubmit={submitEdit}>
        <input
          aria-label="Natural-language schedule edit"
          placeholder="Schedule a review with Sam 3-4pm Thursday"
          value={draft}
          disabled={busy}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          Apply
        </button>
      </form>

      {conflict && (
        <div className="schedule-conflict" role="alert">
          <p>
            <strong>{conflict.attempted.title}</strong> ({timeLabel(conflict.attempted.start_time)}
            –{timeLabel(conflict.attempted.end_time)}) overlaps{" "}
            {conflict.conflicts_with
              .map((c) => `${c.title} (${timeLabel(c.start_time)}–${timeLabel(c.end_time)})`)
              .join(", ")}
            .
          </p>
          <div className="feature-choice-row">
            <button type="button" disabled={busy} onClick={overwrite}>
              Overwrite
            </button>
            <button type="button" disabled={busy} onClick={clearConflict}>
              Keep existing
            </button>
          </div>
        </div>
      )}

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
                    disabled={busy}
                    onClick={() => resolveDisambiguation(outcome.pendingActionId, opt)}
                  >
                    {actionLabel(opt)}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <p>{outcome.kind === "message" ? outcome.text : outcome.answer}</p>
          )}
          <button type="button" className="feature-dismiss" onClick={() => setOutcome(null)}>
            Dismiss
          </button>
        </div>
      )}

      {error && <p className="feature-error">Couldn&apos;t load the schedule: {error}</p>}
      {loading && days.length === 0 && <p className="feature-loading">Loading…</p>}

      {days.map((d) => (
        <section key={d.date} className="schedule-day">
          {view === "week" && <h2>{formatDayLabel(d.date)}</h2>}
          {d.items.length === 0 ? (
            <p className="feature-empty">Nothing scheduled.</p>
          ) : (
            <ul className="schedule-timeline">
              {layoutDay(d.items).map((slot) => (
                <li
                  key={slot.item.id}
                  className="schedule-slot"
                  data-overlaps={slot.overlaps || undefined}
                >
                  <span className="schedule-gutter">
                    {slot.startLabel}
                    <br />
                    {slot.endLabel}
                  </span>
                  <div className="schedule-slot-body">
                    <span className="feature-row-title">{slot.item.title}</span>
                    {slot.item.location && (
                      <span className="feature-row-meta">{slot.item.location}</span>
                    )}
                    {slot.item.notes && (
                      <span className="feature-row-notes">{slot.item.notes}</span>
                    )}
                  </div>
                  <button
                    type="button"
                    className="feature-delete"
                    onClick={() => removeItem(slot.item)}
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}
