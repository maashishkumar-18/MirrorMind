import { describe, expect, it } from "vitest";

import type { MeetingNote } from "../store/meetings";
import { actionItemLine, summarizeMeeting } from "./meetingsView";

function note(over: Partial<MeetingNote>): MeetingNote {
  return {
    id: "mn_1",
    session_id: null,
    raw_transcript: "",
    attendees: [],
    topics: [],
    decisions: [],
    action_items: [],
    follow_ups: [],
    needs_review: false,
    searchable_text: "",
    created_at: "2026-09-08T12:00:00Z",
    updated_at: "2026-09-08T12:00:00Z",
    ...over,
  };
}

describe("actionItemLine", () => {
  it("includes owner + deadline when present", () => {
    expect(actionItemLine({ task: "Ship it", owner: "Bob", deadline: "Friday" })).toBe(
      "Ship it — Bob (by Friday)",
    );
  });
  it("is just the task when owner/deadline are null", () => {
    expect(actionItemLine({ task: "Ship it", owner: null, deadline: null })).toBe("Ship it");
  });
});

describe("summarizeMeeting", () => {
  it("prefers topics, then decisions, then attendees", () => {
    expect(summarizeMeeting(note({ topics: ["release", "budget"] }))).toBe("release, budget");
    expect(summarizeMeeting(note({ decisions: ["Ship Friday"] }))).toBe("Ship Friday");
    expect(summarizeMeeting(note({ attendees: ["Alice"] }))).toBe("with Alice");
  });
  it("falls back to the first transcript line", () => {
    expect(summarizeMeeting(note({ raw_transcript: "Alice: hello\nBob: hi" }))).toBe("Alice: hello");
  });
  it("handles a wholly empty note", () => {
    expect(summarizeMeeting(note({}))).toBe("Untitled meeting");
  });
});
