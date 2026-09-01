import { describe, expect, it } from "vitest";
import {
  CURRENT_IPC_VERSION,
  IPCEnvelopeSchema,
  IPCVersionMismatchError,
  checkVersion,
} from "./envelope.js";

const validEnvelope = {
  version: CURRENT_IPC_VERSION,
  message_type: "request",
  request_id: "req-1",
  timestamp: "2026-09-01T00:00:00Z",
  payload: { foo: "bar" },
};

describe("IPCEnvelopeSchema", () => {
  it("accepts a well-formed envelope", () => {
    const result = IPCEnvelopeSchema.parse(validEnvelope);
    expect(result).toEqual(validEnvelope);
  });

  it("defaults payload to {} when omitted", () => {
    const { payload, ...rest } = validEnvelope;
    const result = IPCEnvelopeSchema.parse(rest);
    expect(result.payload).toEqual({});
  });

  it("defaults version to CURRENT_IPC_VERSION when omitted", () => {
    const { version, ...rest } = validEnvelope;
    const result = IPCEnvelopeSchema.parse(rest);
    expect(result.version).toBe(CURRENT_IPC_VERSION);
  });

  it.each(["request", "response", "event", "error"])(
    "accepts message_type %s",
    (message_type) => {
      expect(() => IPCEnvelopeSchema.parse({ ...validEnvelope, message_type })).not.toThrow();
    }
  );

  it("rejects an unknown message_type", () => {
    expect(() =>
      IPCEnvelopeSchema.parse({ ...validEnvelope, message_type: "not_a_type" })
    ).toThrow();
  });

  it("rejects a missing message_type", () => {
    const { message_type, ...rest } = validEnvelope;
    expect(() => IPCEnvelopeSchema.parse(rest)).toThrow();
  });

  it("rejects a non-integer version", () => {
    expect(() => IPCEnvelopeSchema.parse({ ...validEnvelope, version: 1.5 })).toThrow();
  });

  it("rejects a missing request_id", () => {
    const { request_id, ...rest } = validEnvelope;
    expect(() => IPCEnvelopeSchema.parse(rest)).toThrow();
  });
});

describe("checkVersion", () => {
  it("returns the parsed envelope when the version matches", () => {
    const result = checkVersion(validEnvelope, CURRENT_IPC_VERSION);
    expect(result.version).toBe(CURRENT_IPC_VERSION);
  });

  it("throws IPCVersionMismatchError when the version doesn't match", () => {
    expect(() => checkVersion(validEnvelope, CURRENT_IPC_VERSION + 1)).toThrow(
      IPCVersionMismatchError
    );
  });

  it("still throws a zod error for a malformed envelope, not IPCVersionMismatchError", () => {
    expect(() => checkVersion({ not: "an envelope" })).not.toThrow(IPCVersionMismatchError);
  });
});
