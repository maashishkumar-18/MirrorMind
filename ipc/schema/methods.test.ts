import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  EVENT_SCHEMAS,
  METHOD_CONTRACTS,
  type EventName,
  type MethodName,
} from "./methods.js";

interface Fixture {
  target: string;
  payload: unknown;
}

const FIXTURES: Fixture[] = JSON.parse(
  readFileSync(fileURLToPath(new URL("../fixtures/methods_examples.json", import.meta.url)), "utf-8"),
);

function schemaFor(target: string) {
  const sep = target.indexOf(":");
  const kind = target.slice(0, sep);
  const name = target.slice(sep + 1);
  if (kind === "event") return EVENT_SCHEMAS[name as EventName];
  if (kind === "params" || kind === "result") return METHOD_CONTRACTS[name as MethodName]?.[kind];
  return undefined;
}

describe("methods.ts fixtures", () => {
  it.each(FIXTURES.map((f) => [f.target, f] as const))("%s parses", (_target, fixture) => {
    const schema = schemaFor(fixture.target);
    expect(schema, `no schema for target ${fixture.target}`).toBeDefined();
    expect(() => schema!.parse(fixture.payload)).not.toThrow();
  });
});

describe("coverage — every contract and event has a fixture", () => {
  const targets = new Set(FIXTURES.map((f) => f.target));

  it.each(Object.keys(METHOD_CONTRACTS))("%s has params + result fixtures", (method) => {
    expect(targets.has(`params:${method}`)).toBe(true);
    expect(targets.has(`result:${method}`)).toBe(true);
  });

  it.each(Object.keys(EVENT_SCHEMAS))("%s has an event fixture", (event) => {
    expect(targets.has(`event:${event}`)).toBe(true);
  });
});

describe("strict — extra keys are rejected", () => {
  it("app.status result rejects an unknown field", () => {
    expect(() =>
      METHOD_CONTRACTS["app.status"].result.parse({
        ipc_version: 1,
        model_setup_required: true,
        active_model: null,
        last_exported_at: null,
        degraded: false,
        ready: true,
        surprise: 1,
      }),
    ).toThrow();
  });

  it("chat.send result rejects an unknown field", () => {
    const base = FIXTURES.find((f) => f.target === "result:chat.send")!.payload as Record<
      string,
      unknown
    >;
    expect(() =>
      METHOD_CONTRACTS["chat.send"].result.parse({ ...base, extra: true }),
    ).toThrow();
  });

  it("nested ChatConflictItem rejects an unknown field", () => {
    expect(() =>
      METHOD_CONTRACTS["chat.send"].result.parse({
        session_id: "s",
        turn_index: 1,
        answer: "a",
        confidence: 0.9,
        tier: 1,
        action_type: "schedule",
        retrieve_needed: false,
        retrieval_route: null,
        is_grounded: false,
        grounding_confidence: 0,
        citations: [],
        warnings: [],
        feature: null,
        disambiguation: null,
        conflict: {
          attempted: {
            id: "",
            title: "x",
            start_time: "t",
            end_time: "t",
            location: "",
            bogus: 1,
          },
          conflicts_with: [],
        },
        dismissed_pending: false,
      }),
    ).toThrow();
  });
});
