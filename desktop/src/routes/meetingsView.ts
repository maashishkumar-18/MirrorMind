/**
 * Pure helpers for the Meetings view (Phase 3 Step 3.3d). No React, no store.
 */
import type { MeetingNote } from "../store/meetings";

type ActionItem = MeetingNote["action_items"][number];

export function actionItemLine(ai: ActionItem): string {
  const bits = [ai.task];
  if (ai.owner) bits.push(`— ${ai.owner}`);
  if (ai.deadline) bits.push(`(by ${ai.deadline})`);
  return bits.join(" ");
}

/** A one-line preview for the collapsed row. */
export function summarizeMeeting(n: MeetingNote): string {
  if (n.topics.length) return n.topics.join(", ");
  if (n.decisions.length) return n.decisions[0];
  if (n.attendees.length) return `with ${n.attendees.join(", ")}`;
  const firstLine = n.raw_transcript.split("\n")[0]?.trim() ?? "";
  return firstLine.length > 80 ? `${firstLine.slice(0, 79)}…` : firstLine || "Untitled meeting";
}

export function formatMeetingDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
