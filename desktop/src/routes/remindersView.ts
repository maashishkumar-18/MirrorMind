/**
 * Pure helpers for the Reminders view (Phase 3 Step 3.3). No React, no store.
 */
import type { Reminder } from "../store/reminders";

export interface ReminderGroups {
  overdue: Reminder[];
  today: Reminder[];
  upcoming: Reminder[];
}

const dayKey = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso.slice(0, 10) : d.toISOString().slice(0, 10);
};

/**
 * Group the active reminders. `overdueIds` is the reconciliation overlay — a
 * reminder groups under "overdue" if its id is in that set **or** its
 * `scheduled_time` is already in the past. Each reminder appears exactly once.
 */
export function groupReminders(
  active: Reminder[],
  overdueIds: Set<string>,
  nowIso: string,
): ReminderGroups {
  const now = new Date(nowIso).getTime();
  const todayKey = dayKey(nowIso);
  const groups: ReminderGroups = { overdue: [], today: [], upcoming: [] };

  const byTime = [...active].sort((a, b) => a.scheduled_time.localeCompare(b.scheduled_time));
  for (const r of byTime) {
    const t = new Date(r.scheduled_time).getTime();
    if (overdueIds.has(r.id) || (!Number.isNaN(t) && t < now)) {
      groups.overdue.push(r);
    } else if (dayKey(r.scheduled_time) === todayKey) {
      groups.today.push(r);
    } else {
      groups.upcoming.push(r);
    }
  }
  return groups;
}

/** Local date + time; the raw string back on a parse failure. */
export function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** `<input type="datetime-local">` value (no seconds, local time) → ISO for the backend. */
export function datetimeLocalToIso(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

/** ISO → `<input type="datetime-local">` value in the viewer's local time. */
export function isoToDatetimeLocal(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}
