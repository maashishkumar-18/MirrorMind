/**
 * E2E bridge (Phase 4 Step 4.1a).
 *
 * Stands in for the Tauri Rust shell. Spawns a real `python -m src.backend.main`
 * sidecar, does the newline-delimited `IPCEnvelope` framing on its stdio, and
 * exposes it to the browser over plain HTTP + SSE (no extra npm deps):
 *
 *   POST /rpc      body = an IPCEnvelope request  -> resolves with the
 *                  correlated `response` / `error` frame (by `request_id`)
 *   GET  /events   text/event-stream — every non-correlated frame the backend
 *                  emits (`message_type:"event"`, plus `backend:exit` on child
 *                  exit), so `tauri-shim.js` can feed `listen("backend:message")`
 *
 * The SSE stream is opened by the shim BEFORE the page's `bootstrap.ts` runs, and
 * this process starts buffering frames the instant the child is spawned, so an
 * early `app.ready` / `app.reminders_pending` is never lost (plan R2).
 *
 * Lifecycle is driven by the Playwright fixture (`harness.ts`): `start()` spawns,
 * `restart()` kills + respawns (same data dir — exercises reconciliation),
 * `stop()` tears down.
 */
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

export class BridgeServer {
  /**
   * @param {object} opts
   * @param {string} opts.python      absolute path to the venv python.exe
   * @param {string} opts.dataDir     RAGPIPE_DATA_DIR (fresh per test)
   * @param {string} opts.dbKey       RAGPIPE_DB_KEY (64 hex)
   * @param {string} [opts.fakeLlm]   RAGPIPE_FAKE_LLM fixture path
   * @param {string} [opts.toastFile] RAGPIPE_FAKE_TOAST recording file
   * @param {Record<string,string>} [opts.extraEnv]
   */
  constructor(opts) {
    this.opts = opts;
    this.child = null;
    /** @type {Map<string, (env:any)=>void>} */
    this.pending = new Map();
    /** @type {Set<import('node:http').ServerResponse>} */
    this.sseClients = new Set();
    /** @type {any[]} buffered frames for a client that connects late */
    this.frameLog = [];
    this.server = null;
    this.port = 0;
  }

  async listen() {
    this.server = createServer((req, res) => this._handle(req, res));
    await new Promise((resolve) => this.server.listen(0, "127.0.0.1", resolve));
    this.port = this.server.address().port;
    return this.port;
  }

  _spawnChild() {
    const env = {
      ...process.env,
      RAGPIPE_DATA_DIR: this.opts.dataDir,
      RAGPIPE_DB_KEY: this.opts.dbKey,
      LANGFUSE_PUBLIC_KEY: "",
      LANGFUSE_SECRET_KEY: "",
      PYTHONIOENCODING: "utf-8",
      PYTHONUNBUFFERED: "1",
      // Every model MirrorMind uses is vendored (LFS) — the backend must never
      // reach huggingface.co. Without this, transformers' `AutoTokenizer.from_
      // pretrained(<local path>)` still does a hub revision check that hangs
      // ~60s on a CI runner with slow/blocked outbound HTTPS (the silent gap
      // that timed out the memory-retrieval reingest). Offline = pure local load.
      HF_HUB_OFFLINE: "1",
      TRANSFORMERS_OFFLINE: "1",
      HF_HUB_DISABLE_TELEMETRY: "1",
      ...(this.opts.fakeLlm ? { RAGPIPE_FAKE_LLM: this.opts.fakeLlm } : {}),
      ...(this.opts.toastFile ? { RAGPIPE_FAKE_TOAST: this.opts.toastFile } : {}),
      ...(this.opts.extraEnv ?? {}),
    };
    const child = spawn(this.opts.python, ["-m", "src.backend.main"], {
      cwd: REPO_ROOT,
      env,
      stdio: ["pipe", "pipe", "pipe"],
    });
    createInterface({ input: child.stdout }).on("line", (line) => this._onLine(line));
    child.stderr.on("data", (d) => process.stderr.write(`[backend] ${d}`));
    child.on("exit", (code, signal) => {
      if (this.child === child) {
        this._broadcast({
          __tauriEvent: "backend:exit",
          payload: { code, reason: signal ? "crash" : "clean", snapshot_path: null, will_retry: false },
        });
        this.child = null;
      }
    });
    this.child = child;
  }

  start() {
    if (this.child) throw new Error("backend already running");
    this._spawnChild();
  }

  async restart() {
    await this.kill();
    this._spawnChild();
  }

  kill() {
    const child = this.child;
    this.child = null;
    if (!child) return Promise.resolve();
    return new Promise((resolve) => {
      child.once("exit", () => resolve());
      // graceful: close stdin (EOF) then hard kill after a grace period
      try { child.stdin.end(); } catch { /* ignore */ }
      setTimeout(() => { try { child.kill("SIGKILL"); } catch { /* ignore */ } }, 1500);
    });
  }

  async stop() {
    this._stopped = true;
    await this.kill();
    for (const res of this.sseClients) {
      try { res.destroy(); } catch { /* ignore */ }
    }
    this.sseClients.clear();
    if (this.server) {
      this.server.closeAllConnections?.();
      await new Promise((resolve) => this.server.close(resolve));
    }
  }

  _onLine(line) {
    const trimmed = line.trim();
    if (!trimmed) return;
    let frame;
    try {
      frame = JSON.parse(trimmed);
    } catch {
      process.stderr.write(`[bridge] non-JSON line: ${trimmed}\n`);
      return;
    }
    const kind = frame.message_type;
    if ((kind === "response" || kind === "error") && this.pending.has(frame.request_id)) {
      this.pending.get(frame.request_id)(frame);
      this.pending.delete(frame.request_id);
      return;
    }
    // Everything else is a server-initiated frame for the webview.
    this._broadcast({ __tauriEvent: "backend:message", payload: frame });
  }

  _broadcast(evt) {
    this.frameLog.push(evt);
    const data = `data: ${JSON.stringify(evt)}\n\n`;
    for (const res of this.sseClients) res.write(data);
  }

  _send(envelope) {
    return new Promise((resolve, reject) => {
      if (!this.child) return reject(new Error("backend_unavailable"));
      const rid = envelope.request_id;
      this.pending.set(rid, resolve);
      this.child.stdin.write(JSON.stringify(envelope) + "\n", (err) => {
        if (err) {
          this.pending.delete(rid);
          reject(err);
        }
      });
    });
  }

  /** Fire one IPC method directly (test setup — bypasses the page). Resolves
   * with the response/error frame. */
  async rpc(method, params = {}) {
    return this._send({
      version: 1,
      message_type: "request",
      request_id: `harness-${Math.random().toString(36).slice(2)}`,
      timestamp: new Date().toISOString(),
      payload: { method, params },
    });
  }

  _handle(req, res) {
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Headers", "content-type");
    if (req.method === "OPTIONS") return res.writeHead(204).end();

    if (req.method === "GET" && req.url.startsWith("/events")) {
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      });
      for (const evt of this.frameLog) res.write(`data: ${JSON.stringify(evt)}\n\n`);
      this.sseClients.add(res);
      req.on("close", () => this.sseClients.delete(res));
      return;
    }

    if (req.method === "POST" && req.url === "/rpc") {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", async () => {
        try {
          const envelope = JSON.parse(body);
          const frame = await this._send(envelope);
          res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify(frame));
        } catch (err) {
          res
            .writeHead(200, { "Content-Type": "application/json" })
            .end(JSON.stringify({ __bridgeError: { kind: "transport", message: String(err) } }));
        }
      });
      return;
    }

    res.writeHead(404).end();
  }
}
