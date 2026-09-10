/**
 * Playwright fixtures for the Phase 4 e2e flows (Step 4.1).
 *
 * Each test gets a fresh temp data dir + a real Python backend behind
 * `BridgeServer`, with the LLM stubbed from a per-flow fixture
 * (`RAGPIPE_FAKE_LLM`) and toast calls recorded to a file
 * (`RAGPIPE_FAKE_TOAST`). The page is pre-injected with `tauri-shim.js` so
 * `@tauri-apps/api` talks to that backend.
 */
import { execFile } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { test as base, expect } from "@playwright/test";

import { BridgeServer } from "./bridge-server.mjs";

const execFileP = promisify(execFile);

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..", "..", "..");
const VENV_PYTHON = join(REPO_ROOT, ".venv", "Scripts", "python.exe");
// Local dev uses the repo venv; CI installs into the runner's system python and
// sets MIRRORMIND_BACKEND_PYTHON (or we fall back to `python` on PATH).
const PYTHON =
  process.env.MIRRORMIND_BACKEND_PYTHON ??
  (existsSync(VENV_PYTHON) ? VENV_PYTHON : "python");
// A fixed dev/test key — `src/backend/keys.py` accepts `RAGPIPE_DB_KEY` verbatim.
const DB_KEY = "0".repeat(64);
const FIXTURES_DIR = join(REPO_ROOT, "tests", "e2e", "fixtures");

export interface Backend {
  /** Spawn (or respawn, keeping the data dir) the Python sidecar. */
  start(): void;
  /** Kill + respawn against the same data dir — exercises reconciliation. */
  restart(): Promise<void>;
  /** Kill the sidecar without tearing down the bridge (so `seed` can write). */
  stopChild(): Promise<void>;
  /** Wait until the frontend store has seen `app.ready`. */
  waitReady(): Promise<void>;
  /** Block until the SessionWorker has finished warm-up (loads all-MiniLM +
   *  the cross-encoder — 20–40s on a cold CI runner). A `chat.history` round
   *  trip only resolves once the worker drains its first queue item, so it is
   *  the warm signal. Call after `waitReady()` in chat flows. */
  warmup(): Promise<void>;
  /** Apply a `tools.e2e_seed` spec to the DB (backend must be stopped). */
  seed(spec: Record<string, unknown>): Promise<void>;
  /** The `[op, args]` toast calls recorded so far. */
  toastCalls(): { op: string; args: Record<string, string> }[];
  readonly dataDir: string;
  readonly dbPath: string;
}

export const test = base.extend<{
  /** Per-flow: basename of a file under tests/e2e/fixtures/ (no extension). */
  fakeLlmFixture: string | undefined;
  /** Per-flow: stub model management (RAGPIPE_FAKE_MODELS) for the download flow. */
  fakeModels: boolean;
  /** Per-flow: pre-set the active model so model-gated routes pass. */
  activeModel: string | undefined;
  /** Per-flow: make `_reingest` a no-op (real agent kept) so the async
   *  re-ingest followup can't block the next worker call on a slow runner.
   *  For flows that don't test memory retrieval. */
  stubIngest: boolean;
  backend: Backend;
}>({
  fakeLlmFixture: [undefined, { option: true }],
  fakeModels: [false, { option: true }],
  activeModel: [undefined, { option: true }],
  stubIngest: [false, { option: true }],

  backend: async ({ page, fakeLlmFixture, fakeModels, activeModel, stubIngest }, use) => {
    const dataDir = mkdtempSync(join(tmpdir(), "mm-e2e-"));
    const toastFile = join(dataDir, "toast-calls.jsonl");
    const dbPath = join(dataDir, "session.db");
    const fakeLlm = fakeLlmFixture ? join(FIXTURES_DIR, `${fakeLlmFixture}.json`) : undefined;

    if (activeModel) {
      writeFileSync(
        join(dataDir, "app_config.json"),
        JSON.stringify({ version: 3, active_model: activeModel }),
      );
    }

    const bridge = new BridgeServer({
      python: PYTHON,
      dataDir,
      dbKey: DB_KEY,
      fakeLlm,
      toastFile,
      extraEnv: {
        ...(fakeModels ? { RAGPIPE_FAKE_MODELS: "1" } : {}),
        ...(stubIngest ? { RAGPIPE_E2E_STUB_INGEST: "1" } : {}),
      },
    });
    const port = await bridge.listen();

    await page.addInitScript((p) => {
      (window as unknown as { __E2E_BRIDGE_PORT__: number }).__E2E_BRIDGE_PORT__ = p;
    }, port);
    await page.addInitScript({ path: join(HERE, "tauri-shim.js") });

    const api: Backend = {
      dataDir,
      dbPath,
      start: () => bridge.start(),
      restart: () => bridge.restart(),
      stopChild: () => bridge.kill(),
      waitReady: async () => {
        await page.waitForFunction(
          () => {
            const stores = (
              window as unknown as {
                __MM_STORES__?: { backend: { getState: () => { phase: string } } };
              }
            ).__MM_STORES__;
            return !!stores && stores.backend.getState().phase !== "starting";
          },
          { timeout: 30_000 },
        );
      },
      warmup: async () => {
        // chat.history is worker=True — it only returns once the worker has
        // drained warm-up (DB open + embedding model + cross-encoder load).
        const deadline = Date.now() + 90_000;
        for (;;) {
          const frame = await bridge.rpc("chat.history", {});
          if (frame?.message_type === "response") return;
          if (Date.now() > deadline) {
            throw new Error(`worker did not warm up in 90s: ${JSON.stringify(frame)}`);
          }
          await new Promise((r) => setTimeout(r, 1000));
        }
      },
      seed: async (spec) => {
        const specPath = join(dataDir, "seed.json");
        writeFileSync(specPath, JSON.stringify(spec));
        await execFileP(PYTHON, ["-m", "tools.e2e_seed", dbPath, specPath], {
          cwd: REPO_ROOT,
          env: { ...process.env, RAGPIPE_DB_KEY: DB_KEY },
        });
      },
      toastCalls: () =>
        existsSync(toastFile)
          ? readFileSync(toastFile, "utf-8")
              .split("\n")
              .filter(Boolean)
              .map((l) => JSON.parse(l))
          : [],
    };

    await use(api);

    await bridge.stop();
    rmSync(dataDir, { recursive: true, force: true });
  },
});

export { expect };
