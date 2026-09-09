import { describe, expect, it } from "vitest";

import type { ChatSendResult } from "@ipc/methods";
import type { z } from "zod";

import { actionLabel, interpretCreateResult } from "./featureCreate";

type SendResult = z.infer<typeof ChatSendResult>;

function result(over: Partial<SendResult>): SendResult {
  return {
    session_id: "session_1",
    turn_index: 2,
    answer: "ok",
    confidence: 0.9,
    tier: 1,
    action_type: "reminder",
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

describe("interpretCreateResult", () => {
  it("returns `created` when a feature came back", () => {
    const o = interpretCreateResult(
      result({ feature: { kind: "reminder", id: "rem_1", summary: "Call John" } }),
    );
    expect(o).toEqual({ kind: "created", feature: { kind: "reminder", id: "rem_1", summary: "Call John" } });
  });

  it("returns `disambiguation` with the pending id + options", () => {
    const o = interpretCreateResult(
      result({
        tier: 2,
        disambiguation: { pending_action_id: "pa_1", options: ["reminder", "todo", "conversation"] },
      }),
    );
    expect(o).toEqual({
      kind: "disambiguation",
      pendingActionId: "pa_1",
      options: ["reminder", "todo", "conversation"],
    });
  });

  it("returns `conflict` when the schedule overlapped", () => {
    const conflict = {
      attempted: {
        id: "",
        schedule_id: "",
        title: "Dentist",
        start_time: "t",
        end_time: "t",
        location: "",
        notes: "",
        created_at: "",
        updated_at: "",
      },
      conflicts_with: [],
    };
    const o = interpretCreateResult(result({ action_type: "schedule", answer: "overlaps", conflict }));
    expect(o).toEqual({ kind: "conflict", conflict, answer: "overlaps" });
  });

  it("falls back to `message` for a plain Tier-3/4 answer", () => {
    const o = interpretCreateResult(result({ tier: 3, answer: "Try 'remind me to…'." }));
    expect(o).toEqual({ kind: "message", text: "Try 'remind me to…'." });
  });
});

describe("actionLabel", () => {
  it("maps known action types", () => {
    expect(actionLabel("reminder")).toBe("Set a reminder");
    expect(actionLabel("conversation")).toBe("Just chatting");
  });
  it("passes an unknown value through", () => {
    expect(actionLabel("weird")).toBe("weird");
  });
});
