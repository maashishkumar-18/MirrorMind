import { beforeEach, describe, expect, it } from "vitest";

import { useMeetingStore, type MeetingNote } from "./meetings";

const initial = useMeetingStore.getState();
beforeEach(() => useMeetingStore.setState(initial, true));

function note(over: Partial<MeetingNote> & { id: string }): MeetingNote {
  return {
    session_id: null,
    raw_transcript: "t",
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

describe("useMeetingStore", () => {
  it("prependMeeting puts the new note first", () => {
    useMeetingStore.getState().setMeetings([note({ id: "old" })]);
    useMeetingStore.getState().prependMeeting(note({ id: "new" }));
    expect(useMeetingStore.getState().meetings.map((m) => m.id)).toEqual(["new", "old"]);
  });

  it("capture lifecycle toggles `capturing`", () => {
    useMeetingStore.getState().startCapture();
    expect(useMeetingStore.getState().capturing).toBe(true);
    useMeetingStore.getState().captureDone();
    expect(useMeetingStore.getState().capturing).toBe(false);
  });

  it("removeMeeting drops it", () => {
    useMeetingStore.getState().setMeetings([note({ id: "a" }), note({ id: "b" })]);
    useMeetingStore.getState().removeMeeting("a");
    expect(useMeetingStore.getState().meetings.map((m) => m.id)).toEqual(["b"]);
  });

  it("failLoad clears loading + records the message", () => {
    useMeetingStore.getState().startLoad();
    useMeetingStore.getState().failLoad("nope");
    expect(useMeetingStore.getState().loading).toBe(false);
    expect(useMeetingStore.getState().error).toBe("nope");
  });
});
