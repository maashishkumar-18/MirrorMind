import { describe, expect, it } from "vitest";

import type { SessionMessage } from "../store/session";
import {
  disambigLabel,
  formatCitationLabel,
  formatTimestamp,
  isBoundary,
  shortId,
  withBoundaries,
} from "./chatView";

function msg(over: Partial<SessionMessage> & { id: string; sessionId: string }): SessionMessage {
  return {
    turnIndex: 0,
    role: "user",
    content: "x",
    createdAt: null,
    status: "ok",
    citations: [],
    warnings: [],
    tier: null,
    conflict: null,
    ...over,
  };
}

describe("withBoundaries", () => {
  it("inserts no boundary for a single-session run (incl. the first turn)", () => {
    const rows = withBoundaries([
      msg({ id: "a:0", sessionId: "a" }),
      msg({ id: "a:1", sessionId: "a" }),
      msg({ id: "a:2", sessionId: "a" }),
    ]);
    expect(rows.filter(isBoundary)).toHaveLength(0);
    expect(rows).toHaveLength(3);
  });

  it("inserts exactly one boundary at each session change", () => {
    const rows = withBoundaries([
      msg({ id: "a:0", sessionId: "a" }),
      msg({ id: "a:1", sessionId: "a" }),
      msg({ id: "b:0", sessionId: "b" }),
      msg({ id: "b:1", sessionId: "b" }),
      msg({ id: "c:0", sessionId: "c" }),
    ]);
    expect(rows.filter(isBoundary)).toHaveLength(2);
    // boundary sits before the first turn of the new session
    expect(isBoundary(rows[2])).toBe(true);
    expect((rows[3] as SessionMessage).id).toBe("b:0");
  });

  it("handles an empty transcript", () => {
    expect(withBoundaries([])).toEqual([]);
  });
});

describe("shortId", () => {
  it("strips the session_ prefix and keeps the tail", () => {
    expect(shortId("session_0123456789ab")).toBe("6789ab");
    expect(shortId("session_abc")).toBe("abc");
  });
});

describe("formatTimestamp", () => {
  it("formats a valid ISO string", () => {
    expect(formatTimestamp("2026-03-04T14:15:00")).toMatch(/Mar/);
  });
  it("returns the raw string on a parse failure", () => {
    expect(formatTimestamp("not-a-date")).toBe("not-a-date");
  });
});

describe("formatCitationLabel", () => {
  it("renders [Session <short> · approx. <ts>]", () => {
    const label = formatCitationLabel({
      chunk_id: "chunk-1",
      session_id: "session_0123456789ab",
      approximate_timestamp: "2026-03-04T14:15:00",
    });
    expect(label).toContain("[Session 6789ab · approx.");
  });
});

describe("disambigLabel", () => {
  it("maps known action types", () => {
    expect(disambigLabel("reminder")).toBe("Set a reminder");
    expect(disambigLabel("conversation")).toBe("Just chatting");
  });
  it("falls back to the raw value", () => {
    expect(disambigLabel("weird_new_type")).toBe("weird_new_type");
  });
});
