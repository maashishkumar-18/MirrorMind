/**
 * Chat session state (Phase 3 Step 3.2) — deferred from fe.4.
 *
 * Client-owned, driven entirely by `Chat.tsx` calling `call("chat.*")`:
 *   - mount   → `startHistoryLoad()` / `hydrate()` / `failHistoryLoad()`
 *   - send    → `startSend()` (optimistic) → `completeSend()` | `failSend()`
 *   - Tier 2  → `completeSend` stashes `pendingDisambiguation`; resolved by
 *               `applyConfirmResult()` after `chat.confirm_action`
 *   - new     → `reset()` (keeps the transcript; a boundary shows on the next turn)
 *
 * `messages` is append-only for the lifetime of a `/chat` mount. Each turn carries
 * its own `sessionId`; the render layer draws a session-boundary separator wherever
 * two adjacent turns disagree (covers `chat.new` *and* transparent idle auto-close —
 * there is no "session changed" event).
 */
import type { ChatCitation, ChatConflict, ChatDisambiguation, ChatHistoryResult, ChatSendResult } from "@ipc/methods";
import { create } from "zustand";
import type { z } from "zod";

type Citation = z.infer<typeof ChatCitation>;
type Conflict = z.infer<typeof ChatConflict>;
type Disambiguation = z.infer<typeof ChatDisambiguation>;
type HistoryResult = z.infer<typeof ChatHistoryResult>;
type SendResult = z.infer<typeof ChatSendResult>;

export type MsgStatus = "ok" | "pending" | "failed";

export interface SessionMessage {
  /** Stable React key: `${sessionId}:${turnIndex}` once known, else a temp uid. */
  id: string;
  sessionId: string;
  turnIndex: number | null;
  role: "user" | "assistant";
  content: string;
  createdAt: string | null;
  status: MsgStatus;
  citations: Citation[];
  warnings: string[];
  tier: number | null;
  /** Set on the assistant turn that reported a schedule overlap (rendered inline). */
  conflict: Conflict | null;
}

export interface PendingDisambiguation extends Disambiguation {
  /** id of the assistant message this popup belongs under. */
  forMessageId: string;
}

export interface SessionState {
  sessionId: string | null;
  messages: SessionMessage[];
  /** "awaiting response" — drives the typing indicator; tolerates ~120s. */
  sending: boolean;
  /** true only while the mount `chat.history` call is in flight. */
  historyLoading: boolean;
  /** true once the first `chat.history` resolves (even to an empty session). */
  hydrated: boolean;
  pendingDisambiguation: PendingDisambiguation | null;
  /** Mirror of the latest assistant turn's conflict; the per-message field is the render source. */
  pendingConflict: Conflict | null;
  error: string | null;

  startHistoryLoad: () => void;
  hydrate: (result: HistoryResult) => void;
  failHistoryLoad: () => void;

  startSend: (text: string) => string;
  completeSend: (tempId: string, result: SendResult) => void;
  failSend: (tempId: string, message: string) => void;
  removeMessage: (id: string) => void;

  applyConfirmResult: (result: SendResult) => void;
  dismissDisambiguation: () => void;

  reset: (newSessionId: string) => void;
  clearError: () => void;
}

let _uid = 0;
const tempId = () => `tmp-${Date.now()}-${_uid++}`;

function assistantFrom(result: SendResult): SessionMessage {
  return {
    id: `${result.session_id}:${result.turn_index + 1}`,
    sessionId: result.session_id,
    turnIndex: result.turn_index + 1,
    role: "assistant",
    content: result.answer,
    createdAt: null,
    status: "ok",
    citations: result.citations,
    warnings: result.warnings,
    tier: result.tier,
    conflict: result.conflict,
  };
}

export const useSessionStore = create<SessionState>((set) => ({
  sessionId: null,
  messages: [],
  sending: false,
  historyLoading: false,
  hydrated: false,
  pendingDisambiguation: null,
  pendingConflict: null,
  error: null,

  startHistoryLoad: () => set({ historyLoading: true }),

  hydrate: (result) =>
    set({
      sessionId: result.session_id,
      historyLoading: false,
      hydrated: true,
      messages: result.messages.map((m) => ({
        id: `${result.session_id}:${m.turn_index}`,
        sessionId: result.session_id,
        turnIndex: m.turn_index,
        role: m.role === "assistant" ? "assistant" : "user",
        content: m.content,
        createdAt: m.created_at,
        status: "ok" as const,
        citations: [],
        warnings: [],
        tier: null,
        conflict: null,
      })),
    }),

  failHistoryLoad: () => set({ historyLoading: false }),

  startSend: (text) => {
    const id = tempId();
    set((s) => ({
      sending: true,
      error: null,
      pendingDisambiguation: null,
      pendingConflict: null,
      messages: [
        ...s.messages,
        {
          id,
          sessionId: s.sessionId ?? "",
          turnIndex: null,
          role: "user",
          content: text,
          createdAt: new Date().toISOString(),
          status: "pending",
          citations: [],
          warnings: [],
          tier: null,
          conflict: null,
        },
      ],
    }));
    return id;
  },

  completeSend: (tId, result) =>
    set((s) => {
      const assistant = assistantFrom(result);
      const messages = s.messages.map((m) =>
        m.id === tId
          ? {
              ...m,
              id: `${result.session_id}:${result.turn_index}`,
              sessionId: result.session_id,
              turnIndex: result.turn_index,
              status: "ok" as const,
            }
          : m,
      );
      messages.push(assistant);
      return {
        messages,
        sessionId: result.session_id,
        sending: false,
        pendingConflict: result.conflict ?? null,
        pendingDisambiguation: result.disambiguation
          ? { ...result.disambiguation, forMessageId: assistant.id }
          : null,
      };
    }),

  failSend: (tId, message) =>
    set((s) => ({
      sending: false,
      error: message,
      messages: s.messages.map((m) =>
        m.id === tId ? { ...m, status: "failed" as const } : m,
      ),
    })),

  removeMessage: (id) => set((s) => ({ messages: s.messages.filter((m) => m.id !== id) })),

  applyConfirmResult: (result) =>
    set((s) => ({
      messages: [...s.messages, assistantFrom(result)],
      sessionId: result.session_id,
      pendingDisambiguation: null,
      pendingConflict: result.conflict ?? null,
    })),

  dismissDisambiguation: () => set({ pendingDisambiguation: null }),

  reset: (newSessionId) =>
    set({
      sessionId: newSessionId,
      pendingDisambiguation: null,
      pendingConflict: null,
      error: null,
    }),

  clearError: () => set({ error: null }),
}));
