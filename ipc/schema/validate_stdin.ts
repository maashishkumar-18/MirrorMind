/**
 * CLI entry point for the cross-language IPC round-trip test
 * (tests/common/test_ipc_envelope_roundtrip.py). Reads a JSON envelope from
 * stdin, validates it against IPCEnvelopeSchema, writes the canonicalized
 * (zod-parsed) JSON to stdout. Non-zero exit + an error message on stderr
 * if validation fails.
 *
 * Invoked via `npx tsx ipc/schema/validate_stdin.ts` — not meant to be run
 * interactively.
 */
import { IPCEnvelopeSchema } from "./envelope.js";

function readStdin(): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf-8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

async function main() {
  const raw = await readStdin();
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    process.stderr.write(`Invalid JSON on stdin: ${String(err)}\n`);
    process.exit(1);
  }

  const result = IPCEnvelopeSchema.safeParse(parsed);
  if (!result.success) {
    process.stderr.write(`Envelope validation failed: ${result.error.message}\n`);
    process.exit(1);
  }

  process.stdout.write(JSON.stringify(result.data));
}

main();
