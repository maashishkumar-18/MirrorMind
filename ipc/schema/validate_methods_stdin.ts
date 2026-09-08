/**
 * CLI entry point for the cross-language method round-trip test
 * (tests/common/test_ipc_methods_roundtrip.py). Reads `{ target, payload }`
 * from stdin, validates `payload` against the schema named by `target`, and
 * writes the canonicalized (zod-parsed) JSON to stdout. Non-zero exit + a
 * message on stderr if `target` is unknown or validation fails.
 *
 * `target` is `"<kind>:<name>"`, split on the FIRST `:` only (method names
 * contain dots, never colons):
 *   "params:chat.send"  "result:model.download"  "event:app.ready"
 *
 * Invoked via `npx tsx ipc/schema/validate_methods_stdin.ts` — not interactive.
 */
import { EVENT_SCHEMAS, METHOD_CONTRACTS } from "./methods.js";
import type { EventName, MethodName } from "./methods.js";

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

function resolveSchema(target: string) {
  const sep = target.indexOf(":");
  if (sep < 0) {
    process.stderr.write(`Malformed target (expected "<kind>:<name>"): ${target}\n`);
    process.exit(1);
  }
  const kind = target.slice(0, sep);
  const name = target.slice(sep + 1);

  if (kind === "event") {
    const schema = EVENT_SCHEMAS[name as EventName];
    if (!schema) {
      process.stderr.write(`Unknown event: ${name}\n`);
      process.exit(1);
    }
    return schema;
  }
  if (kind === "params" || kind === "result") {
    const contract = METHOD_CONTRACTS[name as MethodName];
    if (!contract) {
      process.stderr.write(`Unknown method: ${name}\n`);
      process.exit(1);
    }
    return contract[kind];
  }
  process.stderr.write(`Unknown target kind: ${kind}\n`);
  process.exit(1);
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

  const { target, payload } = parsed as { target: string; payload: unknown };
  if (typeof target !== "string") {
    process.stderr.write(`Missing "target" string on stdin\n`);
    process.exit(1);
  }

  const schema = resolveSchema(target);
  const result = schema.safeParse(payload);
  if (!result.success) {
    process.stderr.write(`Validation failed for ${target}: ${result.error.message}\n`);
    process.exit(1);
  }

  process.stdout.write(JSON.stringify(result.data));
}

main();
