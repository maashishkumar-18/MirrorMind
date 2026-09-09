/**
 * Pure helpers for the chat view (Phase 3 Step 3.2). No React, no store —
 * unit-tested directly (vitest runs in the `node` environment; no RTL).
 */
import type { ChatCitation } from "@ipc/methods";
import type { z } from "zod";

import type { SessionMessage } from "../store/session";

type Citation = z.infer<typeof ChatCitation>;

export interface Boundary {
  kind: "boundary";
  id: string;
}

export type ChatRow = SessionMessage | Boundary;

export function isBoundary(row: ChatRow): row is Boundary {
  return (row as Boundary).kind === "boundary";
}

/**
 * Interleave session-boundary markers into the transcript. A boundary is
 * inserted between two adjacent turns whose `sessionId` differs — this is the
 * only signal for `chat.new` *and* a transparent idle auto-close. Walks from
 * index 1, so it can never emit a marker before the first turn.
 */
export function withBoundaries(messages: SessionMessage[]): ChatRow[] {
  const rows: ChatRow[] = [];
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (i > 0 && m.sessionId !== messages[i - 1].sessionId) {
      rows.push({ kind: "boundary", id: `boundary-${m.id}` });
    }
    rows.push(m);
  }
  return rows;
}

/** Trailing chars of a `session_<uuid hex>` id — a compact inline marker. */
export function shortId(sessionId: string): string {
  const tail = sessionId.replace(/^session_/, "");
  return tail.length <= 6 ? tail : tail.slice(-6);
}

/** Local date + short time; the raw string back on a parse failure. */
export function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** `[Session a1b2c3 · approx. Mar 4, 2:15 PM]` — from the ChatCitation payload. */
export function formatCitationLabel(c: Citation): string {
  return `[Session ${shortId(c.session_id)} · approx. ${formatTimestamp(c.approximate_timestamp)}]`;
}

const DISAMBIG_LABELS: Record<string, string> = {
  reminder: "Set a reminder",
  todo: "Add a to-do",
  schedule: "Add to schedule",
  meeting_note: "Capture meeting notes",
  summary_request: "Summarize",
  retrieval_query: "Search my memory",
  conversation: "Just chatting",
  none: "Never mind",
};

export function disambigLabel(option: string): string {
  return DISAMBIG_LABELS[option] ?? option;
}
