/**
 * Pure helpers for the Schedule view (Phase 3 Step 3.3e). No React, no store.
 */
import type { ScheduleItem } from "../store/schedule";

// Date-key math is done in UTC so a non-UTC runner/viewer never rolls a day.

/** `YYYY-MM-DD` of the Monday on or before `dateKey`. */
export function weekStart(dateKey: string): string {
  const d = new Date(`${dateKey}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return dateKey;
  const dow = (d.getUTCDay() + 6) % 7; // Mon = 0
  d.setUTCDate(d.getUTCDate() - dow);
  return d.toISOString().slice(0, 10);
}

export function addDays(dateKey: string, n: number): string {
  const d = new Date(`${dateKey}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return dateKey;
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function formatDayLabel(dateKey: string): string {
  const d = new Date(`${dateKey}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return dateKey;
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function timeLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export interface TimelineSlot {
  item: ScheduleItem;
  overlaps: boolean;
  startLabel: string;
  endLabel: string;
}

/**
 * Order the day's items by start time and flag any whose span overlaps another
 * (the conflict alert is for *new* items, but an existing double-booking should
 * still read as one).
 */
export function layoutDay(items: ScheduleItem[]): TimelineSlot[] {
  const sorted = [...items].sort((a, b) => a.start_time.localeCompare(b.start_time));
  return sorted.map((item, i) => {
    const overlaps = sorted.some(
      (other, j) =>
        j !== i && other.start_time < item.end_time && other.end_time > item.start_time,
    );
    return {
      item,
      overlaps,
      startLabel: timeLabel(item.start_time),
      endLabel: timeLabel(item.end_time),
    };
  });
}

export interface ConflictShape {
  attempted: { title: string; start_time: string; end_time: string; location: string };
  conflicts_with: { id: string; title: string; start_time: string; end_time: string }[];
}

/** The `schedule.create_item` params that resolve a conflict by overwriting. */
export function overwriteParams(conflict: ConflictShape) {
  return {
    title: conflict.attempted.title,
    start_time: conflict.attempted.start_time,
    end_time: conflict.attempted.end_time,
    location: conflict.attempted.location,
    overwrite_ids: conflict.conflicts_with.map((c) => c.id),
  };
}
