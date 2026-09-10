/**
 * Browser-side Tauri IPC shim for the Phase 4 e2e harness.
 *
 * Injected via Playwright `addInitScript` BEFORE any page script, so
 * `@tauri-apps/api`'s `invoke` (→ `window.__TAURI_INTERNALS__.invoke`) and
 * `listen` (→ `plugin:event|listen` + `transformCallback`) work unmodified
 * against the real Python backend proxied by `bridge-server.mjs`.
 *
 * Both directions are wired (plan R2): `invoke("ipc_request", …)` → POST /rpc →
 * correlated reply; every server-initiated frame → SSE → `listen("backend:message")`.
 *
 * Expects `window.__E2E_BRIDGE_PORT__` to be set by a preceding init script.
 */
(() => {
  const PORT = window.__E2E_BRIDGE_PORT__;
  const BASE = `http://127.0.0.1:${PORT}`;

  /** @type {Map<number, Function>} */
  const callbacks = new Map();
  let cbSeq = 0;
  /** @type {{event:string,id:number}[]} */
  const listeners = [];
  let evtSeq = 0;

  function transformCallback(callback, once = false) {
    const id = ++cbSeq;
    callbacks.set(id, (payload) => {
      if (once) callbacks.delete(id);
      callback(payload);
    });
    return id;
  }

  async function invoke(cmd, args = {}) {
    switch (cmd) {
      case "ipc_request": {
        // Honour the per-call timeout the way the real Rust `ipc_request` does —
        // reject with a BridgeError so client.ts routes it to its normal
        // timeout handling instead of hanging forever.
        const ms = typeof args.timeoutMs === "number" ? args.timeoutMs : 0;
        const ctrl = new AbortController();
        const timer = ms > 0 ? setTimeout(() => ctrl.abort(), ms) : null;
        let resp;
        try {
          resp = await fetch(`${BASE}/rpc`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(args.envelope),
            signal: ctrl.signal,
          });
        } catch (e) {
          return Promise.reject({
            kind: e && e.name === "AbortError" ? "timeout" : "transport",
            message: String(e),
          });
        } finally {
          if (timer) clearTimeout(timer);
        }
        const frame = await resp.json();
        if (frame && frame.__bridgeError) {
          return Promise.reject(frame.__bridgeError);
        }
        return frame;
      }
      case "plugin:event|listen": {
        listeners.push({ event: args.event, id: args.handler });
        return args.handler;
      }
      case "plugin:event|unlisten": {
        const i = listeners.findIndex((l) => l.event === args.event && l.id === args.eventId);
        if (i >= 0) listeners.splice(i, 1);
        callbacks.delete(args.eventId);
        return undefined;
      }
      // The frontend never emits, but keep these safe.
      case "plugin:event|emit":
      case "plugin:event|emit_to":
        return undefined;
      default:
        return undefined;
    }
  }

  function dispatch(tauriEvent, payload) {
    for (const l of listeners) {
      if (l.event !== tauriEvent) continue;
      const cb = callbacks.get(l.id);
      if (cb) cb({ event: tauriEvent, id: ++evtSeq, payload });
    }
  }

  const source = new EventSource(`${BASE}/events`);
  source.onmessage = (e) => {
    const evt = JSON.parse(e.data);
    dispatch(evt.__tauriEvent, evt.payload);
  };

  window.__TAURI_INTERNALS__ = {
    invoke,
    transformCallback,
    // present in real Tauri; unused here but referenced by some api paths
    metadata: { currentWindow: { label: "main" }, currentWebview: { label: "main" } },
  };
  window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener: () => {} };
})();
