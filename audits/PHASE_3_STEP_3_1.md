# Phase 3 Step 3.1 — Frontend Architecture and IPC Client

**Status:** the Step 3.1 **backend** (3.1a–3.1d) is complete. The Rust/React scaffold is
split into **fe.1–fe.7**; **fe.1 (`4291182`)** + **fe.2 (`72ba144`)** + **fe.3 (`e678ec4`)** +
**fe.4 (`198d493`)** + **fe.5 (`2198a65`)** + **fe.6 (`b2f7936`, + backend fix `f330e84`)** have
landed. Next: fe.7 (Tauri shell hardening).

Roadmap Step 3.1 bundles the Tauri Rust scaffold, the React+TS+Vite project, the typed zod
IPC client, the degraded-mode banner, and the first-launch model flow — and *implies* a
Python-side transport + dispatcher + backend `main()` the roadmap never spells out. Phases 1
and 2 went backend-first; so does this. Rust/`cargo`/`rustup` are not installed on the dev
machine — a Tauri scaffold needs a toolchain install first — so the frontend sub-step is
sequenced after the backend spine is solid and tested.

---

## Scope split

| Concern | Owner | Status |
|---|---|---|
| Backend entrypoint `main()` — single-instance → key → migrate → integrity-check → open DB → `ShutdownCoordinator` → read loop | Python backend | **3.1a ✅** |
| stdio newline-delimited JSON transport (`IPCEnvelope` in/out) | Python backend | **3.1a ✅** |
| Request dispatcher — version check, typed `params`, worker pool, structured error envelopes | Python backend | **3.1a ✅** |
| IPC **method contract** (`src/common/ipc/methods.py`) — Pydantic `*Params`/`*Result` per method | Python backend | **3.1a ✅** |
| Non-chat methods wiring 1.6/2.1/2.2 primitives: `app.status`, `health.check`, `model.catalog/status/download/activate`, `backup.list/restore`, `app.shutdown` | Python backend | **3.1a ✅** |
| Degraded mode on integrity failure (only status/backup methods served) + `app.integrity_failed` / `app.previous_data_unrecoverable` / `app.ready` events | Python backend | **3.1a ✅** |
| `BackupManager.stage_restore()` (validate-only) + `RestoreResult.validated_snapshot_path` | Python backend | **3.1a ✅** |
| NL slot extraction (`SlotExtractor` — 2nd `simple_generate`, Tier-1 only) + `action_type` → `action_dispatch` → Step 1.5 create handlers; four-tier routing (§3) in `SessionWorker.send()`; `chat.confirm_action` for Tier-2 disambiguation; `ScheduleConflict` surfaced not overwritten | Python backend | **3.1d ✅** |
| **Per-message orchestrator** (`chat.send`, project_logic §4): persist user msg → `RetrievalAgent.reason` → `RetrievalRouter.route` → `GenerationOrchestrator.generate` (short-circuit to `ao.response` when `retrieve_needed=False`) → persist assistant msg → trigger ingestion | Python backend | **3.1b ✅** |
| `sessions`/`messages` persistence (`SessionRepository(TableHandler)`), `chat.new` / `chat.history`, in-memory session state on the `SessionWorker` thread | Python backend | **3.1b ✅** |
| generation routed through the app-config active model (gate: `model_setup_required()` → `no_model_active`; `RetrievalAgent(model=)` / `MetadataExtractor(model=)` / `GenerationOrchestrator(model_name=)`) | Python backend | **3.1b ✅** |
| `SQLiteVectorStore` key wiring — the `SessionWorker` opens `SQLiteVectorStore(db_path, key=key)` on its own thread; `SQLiteVectorStore.close()` added; `coordinator.register("session_worker", …)` teardown (scheduler → session_worker → tracing) | Python backend | **3.1b ✅** |
| idle-session auto-close (§13): lazy check at `chat.send` entry (`RAGPIPE_SESSION_IDLE_MINUTES`, default 45) + `run()`-prologue finalize of dangling sessions + on-launch `ReminderHandler.reconcile_on_launch()` surfaced via `app.reminders_pending` event + `reminders.reconciliation` method | Python backend | **3.1c ✅** |
| `desktop/` Vite + React 19 + TS + Zustand + React Router skeleton; `desktop/src-tauri/` Tauri v2 shell that spawns `python -m src.backend.main` and forwards stdout envelopes to the webview as `backend:message` events | Rust + React | **fe.1 ✅** (`4291182`) |
| `ipc/schema/methods.ts` — zod mirror of `METHOD_CONTRACTS` + the 6 events, `.strict()` throughout; `validate_methods_stdin.ts` + `methods_examples.json` + `tests/common/test_ipc_methods_roundtrip.py` cross-language round-trip | React tooling | **fe.2 ✅** (`72ba144`) |
| Rust **stdio↔invoke bridge** — `ipc_request(envelope, timeoutMs)` command, `request_id` correlation (`tokio::sync::oneshot` map), `response`/`error` → waiters, `event` frames → Tauri events; exit codes 3 → "starting fresh" / 5 → "restore staged" surfaced; `app.shutdown` handshake on window close | Rust shell | **fe.3 ✅** (`e678ec4`) |
| Typed **zod IPC client** (`desktop/src/ipc/client.ts` `call<M>()`) wrapping `@ipc/methods` + `bridge.ts` — zod-validates params + result, `IpcCallError { kind }` + `describeIpcError`, `version_mismatch` → store flag → "please restart" screen, per-method timeouts; `subscribe()` typed event demux; `useBackendStore`/`useModelStore`/`useReminderStore` | React frontend | **fe.4 ✅** (`198d493`) |
| Degraded-mode **banner** (`RootLayout` + `selectBanner`/`selectAppGate` pure selectors) — recovery-mode / reconnecting / unavailable / restoring / "Reconnected" flash; full-screen **gate** for `version_mismatch` + `previous_data_unrecoverable` | React frontend | **fe.5 ✅** (`2198a65`) |
| First-launch model **flow** (`/first-run`: `model.catalog` / `model.status` / `model.download` streaming / `model.activate`) + `<RequireModel>` route guard + Loading → /first-run \| /chat redirect + `no_model_active` → first-run | React frontend | **fe.6 ✅** (`b2f7936`) |
| Tauri **shell hardening** — process supervisor (backoff 1s/2s/4s, **3 restarts after the initial launch = 4 total**) → degraded banner + the "Restart app" action fe.5's banner/gate refer to; single-instance **enforcement** + window focus; **restore file swap** (Rust `fs::rename` of `validated_snapshot_path`, backend down, then relaunch — pairs with 3.1a `stage_restore` + exit 5; resolves `PHASE_2_AUDIT.md` 2.3-C1). `bundle.externalBin` for the PyInstaller sidecar is Phase 5. | Rust shell | **fe.7** |
| Real **WinRT `ToastBridge`** — `cancel_all()` must iterate `RemoveFromSchedule` **per id** (Windows has no bulk-cancel API); the notifier that removes a scheduled toast must be the **same `ToastNotifier` instance** that scheduled it | Rust (called from Python via IPC) | **later** |
| Subprocess-kill **fuzzing test** — 100 iterations on `windows-latest`, kills during **IPC message processing**, **DB write**, and **Ollama inference** (not just idle) → assert detect → restart → banner → recover, zero data loss | Rust + test runner | **later (roadmap Step 4.4)** |
| Step 2.4 **accessibility** (WCAG 2.1 AA / axe-core / Narrator / string externalization) | React frontend | **later Phase 3** |
| PyInstaller `.spec` + freeze verification (sqlcipher3 / keyring / sentence-transformers) | packaging | **Phase 5** |

**One restart *sequence*, two triggers** (`PHASE_2_AUDIT.md` hand-off 2): a backend **crash** →
supervisor relaunches (backoff). A **restore** → supervisor stops the backend (quiescing every
DB connection), *then* `fs::rename`, *then* relaunches. 3.1a delivers the backend half: exit
code 5 + an `app.restore_staged` event carrying `validated_snapshot_path`.

---

## What landed in fe.6 (`b2f7936`; backend fix `f330e84`)

The first-launch model flow (`project_logic §8`, roadmap Step 3.1's last deliverable).

**Backend fix (`f330e84`, separate commit):** `_model_status` (3.1a) called
`OllamaManager.get_model_status(n)` per name, each re-fetching `GET /api/tags` — ~10 HTTP
round-trips (~16 s on a slow-localhost box), past the frontend timeout. New
`OllamaManager.get_model_statuses(names, *, active_model, installed=None)` resolves the whole
list from **one** `/api/tags` (or zero when the caller passes `installed`); `get_model_status`
(singular) is now a thin wrapper (behaviour + tests unchanged). **773 pass** (+1 regression
test); black/ruff/mypy clean.

**Routing.** `Loading` (`/`) is now a splash + redirect: `phase === "starting"` → `<Starting>`;
else → `/first-run` (no active model) \| `/chat`. `<RequireModel>` wraps `/chat` (a nested
layout route). Pure `guardDecision(phase, modelSetupRequired)` → `"starting"` → splash,
`"degraded"`/`"exited"` → **pass** (the fe.5 banner covers it; `model.*` fail in degraded mode
anyway), else `modelSetupRequired ? "first-run" : "pass"`. `client.ts`: a `no_model_active`
error frame flips `useModelStore.modelSetupRequired` → the guard bounces to `/first-run`.

**`/first-run` (`FirstRun.tsx`).** Mount → `Promise.allSettled([call("model.catalog", {}),
call("model.status", {})])` (**independent** — a flaky status must not hide the catalog).
Rows (recommended first, then by size): `display_name` · `formatBytes` · `min_ram_gb` GB RAM
· `description` · a Recommended pill; then by `rowAction(statuses[name])`:
**Download** (disabled while any download runs or `ollama_running === false`) →
`call("model.download", { name }, { timeoutMs: 0 })` with a live progress bar from
`useModelStore.downloadProgress` (the app-lifetime `subscribe`, fe.4); **Activate** →
`call("model.activate", { name })` → `useModelStore.activate` + `navigate("/chat", {replace})`;
**"Currently active"** for an active row. Retry on a catalog-load failure; dismissible
outcome / error lines.

**`useModelStore`** grows `catalog` / `catalogError` / `statuses` / `ollamaRunning` + the
**download lifecycle** (`downloadingModel` / `downloadError` / `downloadOutcome` — in the
store so it survives navigating away from `/first-run` and back). `firstRunReducer` holds
only the transient `activating` / `activateError`.

**Pure logic** — `modelRow.ts` (`rowAction`, `guardDecision`, `formatBytes` / `formatEta` /
`formatSpeed`), `firstRunReducer`, and the store actions — unit-tested; no RTL (settled
decision). New: `routes/{FirstRun.tsx (rewrite), firstRunReducer.ts, modelRow.ts}` + tests,
`ui/{RequireModel,Starting}.tsx`. `npm typecheck`/`lint`/`test` (**65**)/`build` green; `ipc`
suite 79; Rust unchanged. **Verified on `tauri dev`:** `/` → `/first-run`, the 6-model
catalog (installed models show **Activate** — the `model.status` fix), Activate Llama 3.1 8B
→ `app_config.json` `active_model` persisted → navigate to `/chat`; a direct `/chat` nav is
bounced while no model is active; window close still exits code 0, no orphan `python.exe`.

---

## What landed in fe.5 (`2198a65`)

Renders the failure state fe.4 records (`project_logic §7` / deferred Step 2.3).

**`desktop/src/ui/RootLayout.tsx`** — a layout route (`<Route element={<RootLayout/>}>`),
a `height:100vh` flex column: `<Banner>` (flex child, `height` 0 ↔ 2.25rem `transition`) →
`<main class="app-main">` (`flex:1; overflow:auto`) with `<Outlet>` → `<AppGate>`. The banner
**pushes** content — never `position:fixed` over it, no jump. (`Loading`/`FirstRun`/`Chat`
lost their `<main className="screen">` → `<div>`; one `<main>` per page.)

**Pure selectors (exhaustively unit-tested, no RTL — settled decision):**
`selectBanner(BackendState)` → `{ variant, message } | null`
(`recovery-mode` on `phase:"degraded"` → `restore_staged` exit → plain `exited` →
`ipcError`), `selectAppGate(BackendState)` → `{ kind, title, message } | null`
(`version_mismatch` wins over `previous_data_unrecoverable`; the gate **suppresses** the
banner). Order matters: `exit.reason === "restore_staged"` is checked **before**
`phase === "exited"` so a restore shows the blue "Applying your backup…" strip, not the red
"stopped" one.

**`desktop/src/ui/Banner.tsx`** — `useBackendStore(useShallow(selectBanner))`; `data-variant`
CSS colours (reconnecting amber + animated `…`, unavailable/recovery-mode red, restoring blue,
ready green); `role="status"|"alert"` by variant. The **"Reconnected" flash**: a `useEffect`
on `ipcError` (with `clearTimeout` cleanup) shows a 2.5 s green strip on the non-null → null
edge. **`AppGate.tsx`** — `position:fixed; inset:0; z-index:9999`, `role="alertdialog"`,
renders the backend's verbatim `AppPreviousDataUnrecoverableEvent.message` (TS
`PREVIOUS_DATA_FALLBACK` only if the event was missed).

**`client.ts`** — `ipcErrorUi(kind, phase)` extracted (shared by `describeIpcError` +
`selectBanner`; `backend_exited` is `unavailable` only once `phase === "exited"`).
**`store/backend.ts`** — `setLifecycle(reason, message?)` + `lifecycleMessage`;
`phase:"degraded"` stays integrity-failed-only. **`bootstrap.ts`** forwards the
`previous_data_unrecoverable` message and, if the `app.status` probe reports `degraded:true`,
calls `setDegraded([])` (recovers a missed `app.integrity_failed`).

**No `ci.yml` / Rust / Python change.** New: `ui/{RootLayout,Banner,AppGate}.tsx` +
`ui/{bannerState,gateState}.ts` + tests (`bannerState.test.ts` 8, `gateState.test.ts` 5,
`ipcErrorUi` cases). `npm typecheck`/`lint`/`test` (**46**)/`build` green; `ipc` suite 79.
**Verified on `tauri dev`:** healthy → no banner; a corrupted dev `session.db` (HMAC fail on
page 30) → backend `degraded=True` → the red "recovery mode — restore a backup" banner with
content pushed below it; window close still exits code 0, no orphan `python.exe`.

---

## What landed in fe.4 (`198d493`)

The typed layer every feature view (3.2–3.4) calls.

**`desktop/src/ipc/client.ts`** — `call<M extends CallableMethod>(method, params, { timeoutMs? })`
(`CallableMethod` = `MethodName` minus `app.shutdown` — the Rust shell owns that). Flow:
zod-validate `params` against `METHOD_CONTRACTS[m].params` → `ipcRequest` (fe.3 bridge) →
on `invoke` rejection, `isBridgeError` → `IpcCallError { kind: "transport", transportKind }`
→ on a `message_type: "error"` frame, `payload.code === "version_mismatch"` sets
`useBackendStore.versionMismatch`, then `IpcCallError { kind: "backend", code }` (**before**
result validation) → zod-validate the result → `IpcCallError { kind: "schema", phase: "result" }`
on drift (logged under `import.meta.env.DEV`). Every failure **also** records
`useBackendStore.ipcError` (or `versionMismatch`); a **success clears `ipcError`**.
`describeIpcError(err, phase)` → `{ ui: "please_restart" | "temporarily_unavailable" |
"unavailable" }` or **`null`** for a method-specific backend code the caller handles
(`no_model_active` etc.). Per-method timeout table (`chat.send` 120 s, `model.download` 0 =
stream to the Rust ceiling, status/health 5 s, …).

**`desktop/src/ipc/events.ts`** — `subscribe<E extends EventName>(name, handler)`: a
**JS-side demux** over the single `backend:message` stream, keyed by `payload.method`,
`safeParse` against `EVENT_SCHEMAS[name]` — unknown name → `console.warn` + drop; invalid
payload → `console.error` + drop (**never reaches a handler**). One lazily-installed raw
`listen`. Returns an unsubscribe; `_resetSubscriptions()` test seam.

**Stores** — `useBackendStore`: typed `setReady` / `setDegraded` / `setLifecycle` replace the
fe.1 `applyEnvelope`; new `versionMismatch` (sticky) + `ipcError` (`BridgeError["kind"] | null`,
transient). New **`useModelStore`** (`activeModel` / `modelSetupRequired` from `app.ready` +
`app.status`; `downloadProgress` from the event — `catalog`/`statuses` are fe.6) and
**`useReminderStore`** (`overdue` / `pendingAcknowledgment` from `app.reminders_pending`).
`useSessionStore` is Step 3.2's.

**`bootstrap.ts`** reworked onto `subscribe()`; `startBackendBridge()` returns a teardown
`App`'s `useEffect` calls on cleanup (no HMR double-subscribe); it also fires one
`call("app.status")` to recover a missed `app.ready`. **`Loading.tsx`** renders a
**"Please restart MirrorMind"** screen when `versionMismatch` is set (roadmap 3.1 acceptance,
unit-tested) + a zod-validated `call("app.status")` probe.

**Cross-package:** `desktop/` adds `zod` + a `@ipc` alias (`vite.config.ts` `resolve.alias` +
`tsconfig.json` `paths`) → `../ipc/schema`; `ipc/` stays source-only, its `lint-ipc-schema`
job still owns `methods.ts`. **No `ci.yml` change** — `build-frontend` `typecheck` now
compiles `methods.ts` through the alias. **Rust + Python untouched.**

New: `client.ts` + `client.test.ts` (13), `events.test.ts` (5), `store/model.ts` +
`reminders.ts` (+ tests). `npm typecheck`/`lint`/`test` (**30**)/`build` green; `ipc` suite
still 79; `cargo` unchanged. Verified on `tauri dev`: Loading renders the **zod-validated**
`app.status` + typed `app.ready`; window close still exits code 0, no orphan `python.exe`.

---

## What landed in fe.3 (`e678ec4`)

The request/response transport + the graceful shutdown fe.1 lacked.

**Rust (`desktop/src-tauri/src/backend.rs`):** `BackendProcess` → **`BackendBridge`**
managed state — `child` / `stdin` / `pending` (`request_id` → `tokio::sync::oneshot::Sender`)
/ `exit_hint`, per-field `Mutex`.

- **`#[tauri::command] ipc_request(envelope, timeoutMs)`** — registers a oneshot keyed by
  `envelope.request_id` **before** writing the line to stdin, then awaits the correlated reply
  under `tokio::time::timeout` (`timeoutMs == 0` → no timeout; hard **15-min** ceiling always).
  A well-formed backend `error` frame resolves as `Ok(<envelope>)`; only transport failures
  reject, with a serializable **`BridgeError { kind, message }`**, `kind` ∈ `timeout` /
  `backend_exited` (drained on EOF) / `backend_unavailable` (stdin closed) / `transport`.
- **stdout reader** extended in place: `classify_frame(&Value) -> FrameKind` routes by
  `message_type` **only** (never `request_id` — `model.download.progress` events carry one).
  `response`/`error` with a matching pending id → complete the oneshot; unmatched `error` →
  `backend:error`; everything else still → `backend:message`. On EOF: `drain_pending()` (drops
  every sender → pending calls get `backend_exited`), then `backend:exit { code, reason,
  snapshot_path }` — `reason`/`snapshot_path` from an `ExitHint` set when the reader sees
  `app.previous_data_unrecoverable` / `app.restore_staged` (**exit 3 / exit 5 surfaced, not
  acted on** — fe.5/fe.6/fe.7).
- **`lib.rs` `on_window_event(CloseRequested)`**: `api.prevent_close()` → `window.hide()` →
  spawn `graceful_shutdown`: send a **full `IPCEnvelope`** `app.shutdown` frame, poll
  `child.try_wait()` up to **10 s**, then `child.kill()`, then `app.exit(0)`. The
  `RunEvent::ExitRequested | Exit` → `bridge.kill()` stays as an idempotent safety net.
- `#[cfg(test)]` (4): `classify_frame` by message_type, `ExitHint` transitions, the
  `app.shutdown` envelope shape, `now_rfc3339` parseable.

**Frontend:** `desktop/src/ipc/bridge.ts` — thin `ipcRequest(method, params, timeoutMs)`
building a v1 envelope with a **monotonic** `request_id` (not `crypto.randomUUID()` —
`http://tauri.localhost` is not a guaranteed secure context on Windows). `backend:exit` is now
an object (`store/backend.ts` `exit: BackendExit`); `backend:error` recorded on `lastError`.
`startBackendBridge()` moved into an `App` `useEffect` and `vite.config.ts`
`optimizeDeps.include` pins `@tauri-apps/api/{core,event}` — **together these fix a Vite
optimize-deps full-reload race** that surfaced (caught in the e2e) as *"Cannot read properties
of undefined (reading 'transformCallback')"*. `Loading.tsx` runs one `ipcRequest('app.status')`
probe as the fe.3 acceptance surface.

**CI:** `desktop-rust` also runs `cargo test`. **Python untouched** — `app.shutdown` already
works end-to-end (dispatcher inline → `ShutdownCoordinator` → exit 0). New Rust deps: `tokio`
(`sync`, `time`), `time` (`formatting`, `parsing`).

**Verified** on a clean `tauri dev`: the window renders `app.ready` **and** the
`ipc_request(app.status)` round-trip; a window close makes the backend exit **code 0** via its
`ShutdownCoordinator` (not a kill), zero orphan `python.exe`. `cargo fmt`/`clippy`/`check`/
`test` (4) clean; `npm typecheck`/`lint`/`test` (10)/`build` green.

---

## What landed in fe.1 (`4291182`)

`desktop/` — a Vite + React 19 + TypeScript project (React Router, Zustand). Scripts:
`dev` / `build` (`tsc && vite build`) / `typecheck` / `lint` (flat eslint + typescript-eslint) /
`test` (vitest). Routes `/` (Loading), `/first-run`, `/chat` (the last two placeholders for
fe.6 / Step 3.2).

`desktop/src-tauri/` — Tauri v2 (`npm create tauri-app` React-TS template, adapted).
`identifier` `com.mirrormind.companion`, `withGlobalTauri: true`, window 900×680.
`src/backend.rs`: on `setup`, `std::process::Command` spawns the backend child —
interpreter / cwd / repo-root derived from `env!("CARGO_MANIFEST_DIR")` (`<root>/.venv/
Scripts/python.exe`, cwd `<root>`), both overridable via `MIRRORMIND_BACKEND_PYTHON` /
`MIRRORMIND_BACKEND_CWD`; `RAGPIPE_DATA_DIR` passed as an **absolute** `<root>/desktop/
.dev-data` (git-ignored, `create_dir_all`'d before spawn); `LANGFUSE_*` blanked. A reader
thread parses each stdout line as JSON and `app.emit("backend:message", value)`; on child
exit it emits `backend:exit` with the code. `RunEvent::ExitRequested | Exit` → `child.kill()`.
`bootstrap.ts` listens for both events; `useBackendStore` (Zustand) turns `app.ready` /
`app.integrity_failed` into a `phase` the Loading route renders (the fe.1 acceptance surface).

**Known gap (closed in fe.3):** window close does `child.kill()` with **no `app.shutdown`
handshake**, so the backend's `ShutdownCoordinator` teardown does not run in dev — but the
child gets stdin EOF when the shell dies and exits its read loop cleanly (verified: no orphan
`python.exe` after killing the shell). No request/response correlation and no supervisor yet.

CI: `build-frontend` (windows-latest, Node 20 — `npm ci` → typecheck → lint → test → vite
build) and `desktop-rust` (`Swatinem/rust-cache` → `cargo fmt --check` + `clippy -D warnings`
+ `cargo check`, with a stub `dist/index.html` so the check is frontend-build-independent).
Both gate `sign`. `.gitignore` += `desktop/{node_modules,dist,src-tauri/target,src-tauri/gen,
.dev-data}/`.

Verified locally: `cargo fmt`/`check`/`clippy` clean; `npm run tauri dev` opens the window,
the shell spawns the venv backend (cwd + data dir correct), and the webview renders the live
`app.ready` payload (`Backend: Ready`, IPC v1, `model_setup_required: true`, no active model);
killing the shell leaves no orphan process.

## What landed in fe.2 (`72ba144`)

`ipc/schema/methods.ts` — the zod mirror of `src/common/ipc/methods.py`, field-for-field:
all 14 methods' `*Params`/`*Result` + the 6 lifecycle/streaming events
(`app.ready` / `app.integrity_failed` / `app.previous_data_unrecoverable` /
`app.restore_staged` / `app.reminders_pending` / `model.download.progress`). **Every object —
nested DTOs (`ChatCitation`, `ChatConflictItem`, `ReminderWire`, …) included — is `.strict()`**
(matches Pydantic `extra="forbid"`); nullable-with-default fields mirror the Python defaults.
Exports `METHOD_CONTRACTS` (`{params, result, degradedOk, worker}`) + `EVENT_SCHEMAS` for
fe.4's typed client. `methods.py` is the contract source — no reference to internal dataclass
field names.

`ipc/schema/validate_methods_stdin.ts` — CLI validator; `target` is `"<kind>:<name>"`
(`params:` / `result:` / `event:`) split on the **first** `:` so dotted method names never
ambiguate. `ipc/fixtures/methods_examples.json` — one canonical example per params/result/
event, plus extra Tier-1/Tier-2/conflict `chat.send` cases.

`ipc/schema/methods.test.ts` (vitest, 65 cases) — every contract/event has a fixture; each
parses; `.strict()` rejects an extra key incl. a nested one.
`tests/common/test_ipc_methods_roundtrip.py` — the sibling of
`test_ipc_envelope_roundtrip.py`: each fixture is Pydantic-validated + `model_dump`ed, piped
through the zod validator in a real Node subprocess, and the canonical outputs compared;
`skipif` when Node is missing. Imports **only** `src.common.ipc.methods` (pydantic-only) — the
`conftest.py` Langfuse-creds guard is untouched.

`lint-ipc-schema` picks up `methods.ts` + `methods.test.ts` automatically; the `test` job's
`pytest` picks up the round-trip — **no `ci.yml` change**. Verified: `npm typecheck/lint/test
--prefix ipc` green (79 tests); `pytest` 772 passed / 1 skipped (was 728); black/ruff clean.

---

## What landed in 3.1d

**Four-tier routing** in `SessionWorker.send()` (project_logic §3), for an actionable
`action_type` (`reminder` / `todo` / `schedule` / `meeting_note` / `summary_request`):

- **Tier 1 (≥0.85)** — `SlotExtractor.extract()` (a **2nd** `simple_generate`, per-type
  prompt, `recover_json`, `{}` on any failure) → `action_dispatch.dispatch()` → the Step 1.5
  create handler → a **deterministic** confirmation string. No retrieval / generation.
  `meeting_note` self-extracts (its handler's own LLM call) — no slot call.
- **Tier 2 (0.70–0.85)** — stash ONE `_pending_action` (id + action_type + utterance +
  `ao.response`), return `disambiguation={pending_action_id, options}`. The frontend resolves
  via the new **`chat.confirm_action(pending_action_id, choice)`** method (`worker=True`,
  result = `ChatSendResult`). A stale id → `no_pending_action`; **any new `chat.send`
  discards it** and sets `dismissed_pending: true` (no "is this a confirmation?" heuristic —
  the dedicated method removes that ambiguity).
- **Tier 3 (0.50–0.70)** — a one-line clarification with a syntax example
  (`_CLARIFICATIONS` dict).
- **else** (conversation / none / retrieval_query, or actionable at Tier 4) — the existing
  retrieve/generate/short-circuit branch, unchanged.

New modules: `src/backend/slot_extractor.py`, `src/backend/action_dispatch.py`
(`_coerce_iso`: `datetime.fromisoformat` → `dateutil.parser.parse` fallback → `None` →
"missing slot" nudge; **`ScheduleConflict` surfaced, entity not created** — the overwrite/keep
resolution is a documented gap for the Schedule view; **never raises** into `send()`),
`src/common/json_recovery.py::recover_json` (lifts `RetrievalAgent._parse_json`; the agent
delegates. Follow-up `bef8492` repointed `MeetingNoteHandler._parse_json` +
`MetadataExtractor._call_llm` too — **the Step 1.4a "copied 3×" tracking note is closed**).
`MeetingNoteHandler` gains a `model=` ctor (mirrors `MetadataExtractor`). `_pending_action`
is cleared on `new_conversation()` and the idle `_close_session` path.

`ChatSendResult` gains `feature` / `disambiguation` / `conflict` / `dismissed_pending` (all
optional, additive under IPC v1). `requirements.txt` += `python-dateutil` (was transitive,
promoted); `requirements-dev.txt` += `types-python-dateutil`.

`tests/backend/` +29 (`test_json_recovery` / `test_slot_extractor` / `test_action_dispatch`
new; tier + `confirm_action` cases across `test_session_worker` / `test_handlers` /
`test_main`). **699 → 728 tests**, black/ruff/mypy clean. Verified end-to-end in-process:
Tier-1 creates a real `reminders` row; Tier-2 returns `disambiguation` with a
`pending_action_id` and no row.

**Open item carried to Phase 3 Step 3.3 (Schedule view):** the `ScheduleConflict` overwrite /
keep *resolution* is not wired. `create_schedule_item` returns the conflict and 3.1d surfaces
it on `ChatSendResult.conflict`, but nothing acts on an "overwrite" choice. Step 3.3 (or a
small backend step before it) adds the path — on "overwrite", **soft-delete** the conflicting
item(s) then create the new one (project_logic soft-delete-only; **no** `force_create` helper
exists on `ScheduleHandler` today).

---

## What landed in 3.1c

**Idle auto-close** (project_logic §13): `SessionWorker.send()` checks
`now - _last_activity > RAGPIPE_SESSION_IDLE_MINUTES` (default 45, env-overridable — no YAML)
**before** resolving the session; on expiry `_close_session(old, "idle_timeout")` + a fresh
session for the incoming message. `_last_activity` is now stamped at the **end** of a
successful `send()` (was mid-method) so a slow inference never shrinks the next inter-message
gap. `_close_session()` also enqueues a final re-ingest of the closing transcript — **not
synchronous**: `_reingest` reads `messages` (ended_at-independent) so it works from the queue,
and a sync 40-90s drain would stall the user's *returning* message for zero correctness gain
(every prior message already re-ingested). `chat.new` routes through `_close_session` too.

**`run()` prologue** — `SessionRepository.finalize_dangling_sessions("app_shutdown")` (new
method) closes any session a previous crash/kill left open;
`ReminderHandler(connection=conn).reconcile_on_launch(now_iso())` captures the overdue /
pending-ack lists into `self._reconciliation` (written + read only on the worker thread — no
lock; never cleared).

**Surfacing** — the worker fires an `on_ready(reconciliation)` callback once warm-up finishes
→ `main` emits an `app.reminders_pending` **event** whenever either list is non-empty (after
`app.ready`) — the "never silently dropped" guarantee — plus a new `reminders.reconciliation`
**method** (`worker=True`, not `degraded_ok`) to re-fetch. `src/backend/reminders_wire.py` maps
`Reminder` → wire dict, shared by the handler and the event builder. New wire models
`ReminderWire` / `RemindersReconciliation{Params,Result}` / `AppRemindersPendingEvent`.

**No schema change** — `sessions.ended_at` + `close_reason` CHECK already in `0001`;
`finalize_session` writes them today.

**Scheduler ⇄ worker contention** — the `SessionWorker` writes `session_chunks` (re-ingest)
and `sessions` (finalize); the `SchedulerThread` writes only `reminders` and `summaries` and
*reads* `session_chunks` / `sessions` for summary gathering. **No two threads write the same
table.** WAL + `busy_timeout=30000` (both connections, via `open_session_db`) covers a
scheduler summary-read overlapping a worker chunk-write. `sessions` stays single-writer (the
"abandoned session closes on next launch" decision — no SchedulerThread `sessions` writer).

No new deps; `pyproject.toml` / CI unchanged. `tests/backend/` +13 (686 → **699**);
`black` / `ruff` / `mypy src observability db` clean. Verified end-to-end in-process: the
prologue closes a dangling session, `app.reminders_pending` carries a seeded overdue reminder,
`reminders.reconciliation` round-trips.

---

## What landed in 3.1b

`src/backend/session_repository.py` — `SessionRepository(TableHandler)`, the first writer to
`sessions` / `messages` (`create_session` / `finalize_session` / `append_message` [assigns
`turn_index`] / `recent_turns` / `all_messages` / `history`). Keyed connection via
`TableHandler`; `_REQUIRED_TABLES` guard.

`src/backend/session_worker.py` — `SessionWorker(threading.Thread)`: the single-threaded owner
of the session DB connection, the keyed `SQLiteVectorStore`, the model bundle, and the
in-memory session state (`_session_id` / `_turn_count` / `_last_activity`). Warm-up is the
`run()` prologue (an early `chat.send` just waits in the queue). `send()` = the §4 spine:
resolve/gate active model → session identity → persist user msg → `recent_turns` →
`RetrievalAgent.reason` → **branch**: `retrieve_needed` → `RetrievalRouter.route` +
`GenerationOrchestrator.generate`; else return `ao.response` directly (no orchestrator, per
§4). → persist assistant msg → best-effort `MetricsStore` row → enqueue `_reingest` follow-up
(sync during shutdown). `_reingest` guarded like `SchedulerThread._tick`. Model bundle rebuilt
only when `AppConfig.active_model` changes. Injection seams (`agent` / `router` /
`orchestrator` / `pipeline`) so tests never load models.

`src/common/ipc/methods.py` — `chat.send` / `chat.new` / `chat.history` contracts;
`MethodContract` gains a `worker: bool` flag. `src/backend/dispatcher.py` routes `worker=True`
methods to `SessionWorker.submit()` (a worker method with no worker → `unavailable`).
`src/backend/handlers.py` — the three chat handlers + `health.check` rewired through the
worker. `src/backend/main.py` — healthy path closes its own `conn` after the integrity check,
starts the `SessionWorker`, registers `session_worker` teardown (scheduler → session_worker →
tracing); `HandlerContext.conn` is now `| None` (set only in degraded mode).

`src/generation/orchestrator.py` — `GenerationOrchestrator(model_name=)` overrides the resolved
`ModelConfig.model_name`. `src/ingestion/metadata_extractor.py` — `MetadataExtractor(model=,
registry=)` (mirrors `RetrievalAgent`). `src/common/sqlite_vector_store.py` — `close()` +
`_owns_conn`.

No new deps. `pyproject.toml` / CI unchanged. `tests/backend/test_session_repository.py` +
`test_session_worker.py` (fully model-free via injection) + chat cases in
`test_{handlers,dispatcher,main,ipc_methods_contract}.py`. **652 → 686 tests.** `black` /
`ruff` / `mypy src observability db` clean. Verified end-to-end in-process (stubbed LLM):
`chat.new` → 2 `chat.send` (2nd retrieves the 1st) → `chat.history` → exit 0, DB has 1
session / 4 messages / 1 `session_chunks` row after the re-ingest drain.

`conversation_history` non-emptiness on turn 2+ is pinned by
`tests/backend/test_session_worker.py::test_conversation_history_is_populated_on_the_second_turn`
(asserts the worker passes prior turns to both the agent and the orchestrator) on top of the
existing `tests/generation/test_prompt_builder_session.py::test_conversation_history_changes_the_prompt`.

---

## What landed in 3.1a (commit `f4e6f7e`)

New modules under `src/backend/`: `paths.py` (`RAGPIPE_DATA_DIR` → `session_db_path()` /
`snapshot_dir()`), `keys.py` (`resolve_db_key` — Credential Manager via `resolve_key`, with a
`RAGPIPE_DB_KEY` 64-hex dev/test seam), `transport.py` (`StdioTransport` — one envelope per
line, blank-line skip, non-JSON → `None`, thread-safe `send`), `wire.py` (envelope builders,
`HandlerContext`, `MethodError`), `dispatcher.py` (`Dispatcher` — `check_version` →
`version_mismatch`; `ValidationError` → `validation_error`; unknown method → `unknown_method`;
handler crash → `internal_error` and the loop survives; `ThreadPoolExecutor`; `app.shutdown`
handled inline), `handlers.py` (nine thin handlers), `main.py` (the composition + `SIGINT`/
`SIGTERM` → clean shutdown; exit codes `0` / `3` previous-data-unrecoverable / `5` restore
staged).

`src/common/ipc/methods.py` — the frozen method contract: `*Params`/`*Result` Pydantic models
(all `extra="forbid"`), `METHOD_CONTRACTS` registry with a `degraded_ok` flag. Additive under
IPC version 1 — no `CURRENT_IPC_VERSION` bump.

`src/features/backup_manager.py` — `stage_restore()` (the validate-only prefix of `restore()`,
touches no files); `restore()` now delegates its validation to it and is documented as the
tests-only / non-Windows Python-side swap.

`src/common/types.py` — `RestoreResult.validated_snapshot_path: str | None`.

No new dependencies (stdlib only). `pyproject.toml` unchanged — `src/backend/` is already in
the `mypy src` gate with no per-file ignores. CI unchanged — `tests/backend/` is picked up by
the existing `test` job.

Tests: `tests/backend/test_{transport,dispatcher,handlers,ipc_methods_contract,main}.py`
(+56; **652 passing**, was 596; 1 skip = the pre-existing non-Windows single-instance
fallback). `black --check`, `ruff check`, `mypy src observability db` clean.
