/**
 * Versioned IPC envelope schema — the TypeScript/zod counterpart to
 * src/common/ipc/envelope.py. Mirrors it field-for-field. The two are kept
 * in sync by a real cross-language round-trip test
 * (tests/common/test_ipc_envelope_roundtrip.py), not by convention alone.
 */
import { z } from "zod";

export const CURRENT_IPC_VERSION = 1;

export const IPCMessageType = z.enum(["request", "response", "event", "error"]);
export type IPCMessageType = z.infer<typeof IPCMessageType>;

export const IPCEnvelopeSchema = z.object({
  version: z.number().int().default(CURRENT_IPC_VERSION),
  message_type: IPCMessageType,
  request_id: z.string(),
  timestamp: z.string(),
  payload: z.record(z.string(), z.any()).default({}),
});
export type IPCEnvelope = z.infer<typeof IPCEnvelopeSchema>;

/**
 * Raised (as a plain Error) when a well-formed envelope's version doesn't
 * match what this process expects. Distinct from a zod ZodError (malformed
 * envelope) — this is a valid envelope at the wrong version. Mirrors
 * Python's IPCVersionMismatchError.
 */
export class IPCVersionMismatchError extends Error {
  constructor(got: number, expected: number) {
    super(`IPC version mismatch: got ${got}, expected ${expected}`);
    this.name = "IPCVersionMismatchError";
  }
}

export function checkVersion(raw: unknown, expected: number = CURRENT_IPC_VERSION): IPCEnvelope {
  const envelope = IPCEnvelopeSchema.parse(raw);
  if (envelope.version !== expected) {
    throw new IPCVersionMismatchError(envelope.version, expected);
  }
  return envelope;
}
