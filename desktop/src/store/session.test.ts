import { beforeEach, describe, expect, it } from "vitest";
import type { z } from "zod";

import type { ChatHistoryResult, ChatSendResult } from "@ipc/methods";
import { useSessionStore } from "./session";

type SendResult = z.infer<typeof ChatSendResult>;
type HistoryResult = z.infer<typeof ChatHistoryResult>;

const initial = useSessionStore.getState();
beforeEach(() => useSessionStore.setState(initial, true));

function sendResult(over: Partial<SendResult> = {}): SendResult {
  return {
    session_id: "session_aaa",
    turn_index: 0,
    answer: "hi there",
    confidence: 0.9,
    tier: 1,
    action_type: "conversation",
    retrieve_needed: false,
    retrieval_route: null,
    is_grounded: false,
    grounding_confidence: 0,
    citations: [],
    warnings: [],
    feature: null,
    disambiguation: null,
    conflict: null,
    dismissed_pending: false,
    ...over,
  };
}

function historyResult(over: Partial<HistoryResult> = {}): HistoryResult {
  return {
    session_id: "session_aaa",
    messages: [
      { turn_index: 0, role: "user", content: "one", created_at: "2026-01-01T10:00:00" },
      { turn_index: 1, role: "assistant", content: "two", created_at: "2026-01-01T10:00:05" },
    ],
    ...over,
  };
}

describe("useSessionStore", () => {
  it("hydrate replaces messages and flips the loading flags", () => {
    const s = useSessionStore.getState();
    s.startHistoryLoad();
    expect(useSessionStore.getState().historyLoading).toBe(true);

    s.hydrate(historyResult());
    const after = useSessionStore.getState();
    expect(after.historyLoading).toBe(false);
    expect(after.hydrated).toBe(true);
    expect(after.sessionId).toBe("session_aaa");
    expect(after.messages.map((m) => m.content)).toEqual(["one", "two"]);
    expect(after.messages[0].id).toBe("session_aaa:0");
  });

  it("failHistoryLoad leaves hydrated false", () => {
    const s = useSessionStore.getState();
    s.startHistoryLoad();
    s.failHistoryLoad();
    expect(useSessionStore.getState().historyLoading).toBe(false);
    expect(useSessionStore.getState().hydrated).toBe(false);
  });

  it("startSend pushes a pending user turn, sets sending, clears a stale popup", () => {
    useSessionStore.setState({
      pendingDisambiguation: {
        pending_action_id: "pa-1",
        options: ["reminder", "conversation"],
        forMessageId: "session_aaa:1",
      },
    });
    const id = useSessionStore.getState().startSend("remind me");
    const s = useSessionStore.getState();
    expect(s.sending).toBe(true);
    expect(s.pendingDisambiguation).toBeNull();
    const m = s.messages.find((x) => x.id === id);
    expect(m).toMatchObject({ role: "user", content: "remind me", status: "pending" });
  });

  it("completeSend reconciles the user turn, appends the assistant turn, clears sending", () => {
    const id = useSessionStore.getState().startSend("hey");
    useSessionStore.getState().completeSend(
      id,
      sendResult({
        session_id: "session_bbb",
        turn_index: 4,
        answer: "hello",
        tier: 4,
        citations: [
          { chunk_id: "c1", session_id: "session_xyz", approximate_timestamp: "2026-01-02T09:00:00" },
        ],
      }),
    );
    const s = useSessionStore.getState();
    expect(s.sending).toBe(false);
    expect(s.sessionId).toBe("session_bbb");
    const user = s.messages[0];
    expect(user).toMatchObject({ status: "ok", turnIndex: 4, sessionId: "session_bbb" });
    const assistant = s.messages[1];
    expect(assistant).toMatchObject({ role: "assistant", content: "hello", turnIndex: 5, tier: 4 });
    expect(assistant.citations).toHaveLength(1);
  });

  it("completeSend with disambiguation binds the popup to the assistant message", () => {
    const id = useSessionStore.getState().startSend("do the thing");
    useSessionStore.getState().completeSend(
      id,
      sendResult({
        tier: 2,
        answer: "did you want a reminder?",
        disambiguation: { pending_action_id: "pa-9", options: ["reminder", "todo", "conversation"] },
      }),
    );
    const s = useSessionStore.getState();
    expect(s.pendingDisambiguation?.pending_action_id).toBe("pa-9");
    expect(s.pendingDisambiguation?.forMessageId).toBe(s.messages[1].id);
  });

  it("completeSend with a conflict sets both the message field and the mirror", () => {
    const conflict = {
      attempted: { id: "s1", title: "Review", start_time: "t", end_time: "t2", location: "" },
      conflicts_with: [
        { id: "s2", title: "Standup", start_time: "t", end_time: "t2", location: "" },
      ],
    };
    const id = useSessionStore.getState().startSend("schedule a review 2-3");
    useSessionStore.getState().completeSend(id, sendResult({ tier: 1, action_type: "schedule", conflict }));
    const s = useSessionStore.getState();
    expect(s.messages[1].conflict).toEqual(conflict);
    expect(s.pendingConflict).toEqual(conflict);
  });

  it("failSend marks the user turn failed, clears sending, sets error", () => {
    const id = useSessionStore.getState().startSend("hey");
    useSessionStore.getState().failSend(id, "no reply");
    const s = useSessionStore.getState();
    expect(s.sending).toBe(false);
    expect(s.error).toBe("no reply");
    expect(s.messages[0].status).toBe("failed");
  });

  it("applyConfirmResult appends the assistant turn and clears the popup", () => {
    useSessionStore.setState({
      pendingDisambiguation: {
        pending_action_id: "pa-1",
        options: ["reminder", "conversation"],
        forMessageId: "x",
      },
    });
    useSessionStore.getState().applyConfirmResult(sendResult({ answer: "reminder set", tier: 1 }));
    const s = useSessionStore.getState();
    expect(s.pendingDisambiguation).toBeNull();
    expect(s.messages[s.messages.length - 1]).toMatchObject({
      role: "assistant",
      content: "reminder set",
    });
  });

  it("dismissDisambiguation clears only the popup", () => {
    useSessionStore.setState({
      pendingDisambiguation: { pending_action_id: "p", options: [], forMessageId: "x" },
      messages: [],
    });
    useSessionStore.getState().dismissDisambiguation();
    expect(useSessionStore.getState().pendingDisambiguation).toBeNull();
  });

  it("reset swaps the session id but keeps the transcript", () => {
    const id = useSessionStore.getState().startSend("hey");
    useSessionStore.getState().completeSend(id, sendResult());
    useSessionStore.getState().reset("session_new");
    const s = useSessionStore.getState();
    expect(s.sessionId).toBe("session_new");
    expect(s.messages).toHaveLength(2);
  });
});
