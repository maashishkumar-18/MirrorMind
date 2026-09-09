# Phase 3 — Frontend Implementation: consolidated as-built reference

**Status:** Phase 3 is **COMPLETE** — Steps 3.1, 3.2, 3.3, 3.4 all landed on
`main` (HEAD `44b0cbe`). **939 py + 7 Rust + 149 vitest (desktop) + 160 (ipc)**;
`black` / `ruff` / `mypy src observability db` clean; desktop + `cargo` builds
green.

**What this file is.** The merged as-built scope map for every Phase 3 sub-step
(it replaces the former per-step files `PHASE_3_STEP_3_{1,2,3,4}.md`). Each
`## Step 3.x` section below is that step's original write-up, lightly
de-duplicated; sub-headings within a step were demoted one level.

**What this file is *not* — yet.** Unlike `PHASE_0_AUDIT.md` / `PHASE_1_AUDIT.md`
/ `PHASE_2_AUDIT.md`, the **independent comprehensive audit of Phase 3 has not
been performed**. That is the next task: fresh-context agents (one per step / per
concern, no access to the implementation's reasoning) that run the suites, drive
`tauri dev` over the WebView2 CDP seam, corrupt state, and try to falsify the
claims below — then a written findings report with PASS / CONCERN / FINDING
tiers and a remediation commit. Per the `PHASE_2_AUDIT.md` model, those findings
get appended to *this* file when they exist.

**Commits:**

| Step | Commits |
|---|---|
| 3.1 backend | `f4e6f7e` (3.1a) / `c410983` (3.1b) / `596c0a4` (3.1c) / `388aadc` (3.1d) |
| 3.1 scaffold | `4291182` (fe.1) / `72ba144` (fe.2) / `e678ec4` (fe.3) / `198d493` (fe.4) / `2198a65` (fe.5) / `b2f7936` + `f330e84` (fe.6) / `fb084df` (fe.7) |
| 3.2 | `31fc72e` |
| 3.3 | `f6474c5` (3.3a) / `ad3d9ad` (3.3b) / `5de2234` (3.3c) / `53dd07d` (3.3d) / `93d560c` (3.3e) |
| 3.4 | `84414b5` (3.4a) / `a823891` (3.4b) / `12a0a1c` (3.4c) / `5bbc649` (3.4d) / `0a6ab4a` (3.4e) / `44b0cbe` (AppConfig concurrency fix) |

---

## Carried forward — for the Phase 3 audit and Phase 4

- **Real WinRT `ToastBridge`** — still stubbed (`NoOpToastBridge` everywhere). No
  OS toast is ever scheduled yet, so the `action_dispatch` / feature-CRUD create
  paths and `data.wipe`'s `cancel_all()` are all inert on the toast side. When it
  lands, `cancel_all()` must iterate `RemoveFromSchedule` per id (no bulk API)
  from the *same* `ToastNotifier` instance that scheduled each toast.
- **`diagnostics.metrics` `error_rate` / `compute_ms`** — reported as `null`
  ("not tracked yet, v1.1"). `SessionWorker._record_metrics` would need to also
  write `refused` / `total_time_ms` / `compute_ms`.
- **WCAG 2.1 AA / Narrator accessibility pass** (former roadmap Step 2.4) — not
  started; a whole-frontend item folded forward into Phase 3's tail / Phase 4.
- **Subprocess-kill fuzzing test** (roadmap Step 4.4) — 100 iterations on
  `windows-latest`, kills during IPC processing / DB write / Ollama inference.
- **`tauri dev` live coverage gaps** — the native save dialog, `data.wipe` ->
  `window.location.reload`, the `backup.restore` -> exit 5 -> supervisor swap
  round-trip from the Settings panel, and the `opener` reveal / `mailto` scope
  behaviour were not exercised live (this box runs Settings but can't load
  `llama3.1:8b`); covered by unit tests + `ipc` fixtures + an in-process e2e.
- **Step 2.5 performance benchmarking** — deferred to pre-Phase-5 (reference
  hardware). See the status memory's deferral note.
- **`stage_restore` consumes the user's backup file** (fe.7 known follow-up) —
  `fs::rename` moves it; the pre-migration snapshot repopulates `backups/` on
  relaunch, but a dedicated staging copy belongs in a later step.


---

## Step 3.1 — Frontend Architecture and IPC Client

Roadmap Step 3.1 bundles the Tauri Rust scaffold, the React+TS+Vite project, the typed zod
IPC client, the degraded-mode banner, and the first-launch model flow — and *implies* a
Python-side transport + dispatcher + backend `main()` the roadmap never spells out. Phases 1
and 2 went backend-first; so does this. Rust/`cargo`/`rustup` are not installed on the dev
machine — a Tauri scaffold needs a toolchain install first — so the frontend sub-step is
sequenced after the backend spine is solid and tested.

---

### Scope split

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
| Tauri **shell hardening** — process supervisor (backoff 1s/2s/4s, **3 restarts after the initial launch = 4 total**) → degraded banner + the "Restart app" action fe.5's banner/gate refer to; single-instance **enforcement** + window focus; **restore file swap** (Rust `fs::rename` of `validated_snapshot_path`, backend down, then relaunch — pairs with 3.1a `stage_restore` + exit 5; resolves `PHASE_2_AUDIT.md` 2.3-C1). `bundle.externalBin` for the PyInstaller sidecar is Phase 5. | Rust shell | **fe.7 ✅** (`fb084df`) |
| Real **WinRT `ToastBridge`** — `cancel_all()` must iterate `RemoveFromSchedule` **per id** (Windows has no bulk-cancel API); the notifier that removes a scheduled toast must be the **same `ToastNotifier` instance** that scheduled it | Rust (called from Python via IPC) | **later** |
| Subprocess-kill **fuzzing test** — 100 iterations on `windows-latest`, kills during **IPC message processing**, **DB write**, and **Ollama inference** (not just idle) → assert detect → restart → banner → recover, zero data loss | Rust + test runner | **later (roadmap Step 4.4)** |
| Step 2.4 **accessibility** (WCAG 2.1 AA / axe-core / Narrator / string externalization) | React frontend | **later Phase 3** |
| PyInstaller `.spec` + freeze verification (sqlcipher3 / keyring / sentence-transformers) | packaging | **Phase 5** |

**One restart *sequence*, two triggers** (`PHASE_2_AUDIT.md` hand-off 2): a backend **crash** →
supervisor relaunches (backoff). A **restore** → supervisor stops the backend (quiescing every
DB connection), *then* `fs::rename`, *then* relaunches. 3.1a delivers the backend half: exit
code 5 + an `app.restore_staged` event carrying `validated_snapshot_path`.

---

### What landed in fe.7 (`fb084df`) — Step 3.1 scaffold COMPLETE

The last scaffold sub-step. The Rust shell now *recovers* the backend instead of
leaving the app permanently dead on a crash, enforces a single instance, and
performs the restore file swap (resolves `PHASE_2_AUDIT.md` 2.3-C1).

**Supervisor (`desktop/src-tauri/src/backend.rs`).**

- `decide_on_exit(reason, code, clean, restart_count) -> ExitAction` — pure,
  unit-tested. Deliberate exits **first**: `restore_staged` / code 5 → `Restore`;
  `previous_data_unrecoverable` / code 3 → `PreviousDataUnrecoverable` (the fe.5
  gate covers it, no restart); `clean` → `CleanExit`; else a crash →
  `Retry(BACKOFF_MS[restart_count])` (`[1000, 2000, 4000]`) for `MAX_RESTARTS = 3`
  respawns after the initial launch, then `GaveUp`.
- `BackendBridge` gained a 5th per-field `Mutex<Supervisor>` (`restart_count` /
  `clean_shutdown` / `gave_up`). `respawn()` swaps the child + reader thread,
  `pending.clear()`, `exit_hint = default()`, but **keeps** `supervisor`.
  `restart_count` + `gave_up` reset on the next `app.ready` (a backend that can't
  reach ready is genuinely broken — a fast crash-loop *should* hit the ceiling).
- Bounded post-EOF reap: `try_wait()` loop ≤ `REAP_BOUND` (2s), then `kill()` —
  a wedged pipe can't block the supervisor.
- `#[tauri::command] restart_backend` — **gated on `gave_up`** (only reachable
  from the give-up banner, never races a mid-backoff retry). Resets the
  supervisor, `kill()`, emits `backend:exit { reason: "manual_restart",
  will_retry: true }` (so the UI leaves the terminal phase), then `respawn()`; on
  respawn failure sets `gave_up` + emits `respawn_failed` (re-reds the banner).
- `perform_restore_swap(snapshot)` — `fs::rename(snapshot, data_dir()/session.db)`;
  on **any** error → `fs::copy` + best-effort `remove_file`; then best-effort
  `remove_file` of `session.db-wal` / `-shm`. The `Restore` exit is emitted with
  `will_retry: true` so `app.ready` from the relaunched backend clears the banner.
- `build_child()` / `data_dir()` factored out of `spawn()` so `respawn` and
  `perform_restore_swap` share one definition of the interpreter/cwd/data dir.

**Single instance (`lib.rs`).** `tauri-plugin-single-instance = "2"` registered
as the **first** builder plugin; its callback does
`unminimize().ok(); show().ok(); set_focus().ok()` on the `main` window. The
Python `SingleInstanceGuard` stays as defense-in-depth (covers
`python -m src.backend.main` run directly). `restart_backend` added to
`invoke_handler`.

**Backend (`src/backend/dispatcher.py`).** `backup.restore` joins `app.shutdown`
in a new `_INLINE_METHODS` frozenset — it runs **synchronously on the read
thread** instead of the executor, so the serve loop in `main._serve` sees
`dispatcher.shutdown_requested` set the instant `handle_raw` returns.

> **Bug this fixed.** The old code did `self._executor.submit(self._run, …)` for
> `backup.restore`. `handle_raw` returned before the handler ran, the loop's
> `if dispatcher.shutdown_requested.is_set()` check saw `False`, and the loop
> went back to `transport.read_messages()` — a **blocking `stdin` read**. The
> real Tauri shell keeps the child's stdin pipe open, so exit 5 never fired and
> the restore hung forever. `test_backup_restore_stages_and_exits_5` only passed
> because its `BytesIO` input hit EOF immediately and `dispatcher.close(wait=True)`
> in the `finally` drained the executor. New regression:
> `test_backup_restore_runs_inline_so_the_serve_loop_sees_exit_5` asserts the
> flags are set before `handle_raw` returns, with no executor drain.

**Frontend.**

- `ipc/events.ts`: `BackendExit` gained `will_retry: boolean`.
- `store/backend.ts`: `restarting` flag + `setRestarting(v)`. `setRestarting(true)`
  **supersedes a terminal exit** — drops `phase` back to `"starting"` and clears
  `exit`, so the next `app.ready` recovers. `setReady` also clears `restarting`
  and any pending `lifecycle` / `lifecycleMessage`.
- `bootstrap.ts`: `backend:exit { will_retry: true }` → `setRestarting(true)`
  (stay on route, amber banner); `will_retry: false` → `setExited` (terminal).
- `ui/bannerState.ts`: `restarting && lifecycle === "restore_staged"` →
  `restoring` ("Applying your backup — MirrorMind will restart…"); a terminal
  `phase === "exited"` → `unavailable` (checked before the plain `restarting`
  case, so a give-up beats a stale flag).
- `ui/Banner.tsx`: the `unavailable` banner renders a real **"Restart"** button
  (`invoke("restart_backend")`, disabled + "Restarting…" while in flight).

**Known follow-up (documented in the commit + status memory).** `stage_restore`
returns the user's *backup file* as `validated_snapshot_path`, so `fs::rename`
**consumes it**. The migration runner's automatic pre-migration snapshot
repopulates `backups/` on the relaunch, so the directory isn't left empty, but a
dedicated staging copy (so the restored-from backup survives) belongs in a later
backup / Settings step.

**Verification.** `cargo fmt` / `clippy --all-targets -D warnings` / `cargo test`
(7); `desktop` typecheck / lint / test (71) / build; `ipc` (79); Python
`black` / `ruff` / `mypy` + `pytest` (774, +1). E2e via `tauri dev` with a model
active (lands on `/chat`), driven over a WebView2 `--remote-debugging-port=9222`
CDP seam:

- **crash → restart**: `Stop-Process python` → `Retry(1000)` → respawn →
  `app.ready` clears the amber banner, user stays on `/chat`.
- **give up → Restart**: rename `python.exe` aside → respawn fails → `gave_up` →
  red "MirrorMind's AI backend stopped." banner + a visible, working **Restart**
  button (CDP `Page.captureScreenshot` confirms it; the OS screenshot helper
  mis-crops the DPR-1.25 webview). Restore `python.exe`, click → respawn →
  `app.ready`, banner clears.
- **single instance**: launching a 2nd `target/debug/mirrormind.exe` exits
  immediately; the first stays up.
- **restore swap**: `backup.restore` over IPC → backend exits **code 5**
  (`reason=Some("restore_staged")`, Rust `action=Restore`) → `fs::rename` →
  respawn → `app.ready` with integrity passing. `session.db` is byte-identical
  to the backup afterward; the "Applying your backup…" banner shows during the
  swap and clears on ready.
- **clean close**: WM_CLOSE → `graceful_shutdown` (`ShutdownCoordinator` teardown
  in the log) → `app.exit(0)`; zero orphan `mirrormind` / `python` / `node`. No
  supervisor restart fires (`clean_shutdown` suppresses it).

---

### What landed in fe.6 (`b2f7936`; backend fix `f330e84`)

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

### What landed in fe.5 (`2198a65`)

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

### What landed in fe.4 (`198d493`)

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

### What landed in fe.3 (`e678ec4`)

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
  `app.previous_data_unrecoverable` / `app.restore_staged` (in fe.3 exit 3 / exit 5 were
  surfaced but not acted on; **fe.7's supervisor now acts on both** — exit 3 → the fe.5 gate,
  exit 5 → `perform_restore_swap` + respawn).
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

### What landed in fe.1 (`4291182`)

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

### What landed in fe.2 (`72ba144`)

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

### What landed in 3.1d

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

### What landed in 3.1c

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

### What landed in 3.1b

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

### What landed in 3.1a (commit `f4e6f7e`)

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

---

## Step 3.2 — Chat Interface

Roadmap Step 3.2 = the primary chat surface on top of the fe.1–fe.7 scaffold: the
`/chat` view, `useSessionStore` (deferred from fe.4), the Tier-2 disambiguation popup
resolved via `chat.confirm_action`, the Tier-3 clarification (a normal assistant
message, no modal), and the schedule-conflict notice surfaced inline.

Settled design decisions built to:
- **Schedule conflict** — 3.2 only *surfaces* `ChatSendResult.conflict` as an inline
  block under the assistant turn. The overwrite/keep flow + backend resolution +
  `ScheduleHandler` overwrite helper are Step 3.3.
- **Non-streaming** — `chat.send` resolves with the full answer; the typing indicator
  is a plain "awaiting response" state (client timeout already 120 000 ms). No
  `chat.send.progress` event.
- **No virtualization** — the full transcript for the current mount is rendered.
- **Citations** — inline `[Session <short> · approx. <ts>]` markers with a
  hover/focus tooltip built from the `ChatCitation` payload. No
  `chat.citation_context` backend method in 3.2 (a real "jump to session context"
  click-through waits for a later step).

---

### What landed

#### Backend — `src/backend/session_repository.py`

`SessionRepository.history()` now returns the **newest `_HISTORY_MAX` (200) turns**,
oldest-first (`ORDER BY turn_index DESC LIMIT 200` then `reversed`). A defensive
ceiling on the un-virtualized transcript — sessions are already bounded by
idle-close + `chat.new`, so this rarely bites. `all_messages()` (the re-ingest
input) is **untouched and uncapped**. No IPC contract / `methods.py` / `methods.ts`
change — an internal server-side constant, not a parameter. `session_worker.history()`
and `_chat_history` pass the list straight through, unchanged.

`tests/backend/test_session_repository.py` +1
(`test_history_caps_at_the_newest_history_max_turns`: seed 250, assert 200 returned
oldest-first starting at `turn_index == 50`, `all_messages` still 250). **774 → 775
py**, black / ruff / mypy clean.

#### Frontend — `desktop/`

Vitest runs in the `node` environment — **no RTL** (fe.5/fe.6 settled decision).
All logic is a Zustand store + pure helpers, unit-tested directly; the `.tsx`
components are thin wrappers (untested, like `FirstRun.tsx`).

**`desktop/src/store/session.ts` — `useSessionStore`** (new; deferred from fe.4).
Client-owned, driven by `Chat.tsx`. `messages` is **append-only for the lifetime of
a `/chat` mount** — each `SessionMessage` carries its own `sessionId`, and the render
layer draws a boundary wherever two adjacent turns disagree. State:
`sessionId` / `messages` / `sending` / `historyLoading` / `hydrated` /
`pendingDisambiguation` (bound to the assistant message id via `forMessageId`) /
`pendingConflict` (a mirror of the latest turn's conflict — the per-message
`conflict` field is the render source) / `error`. Actions:
`startHistoryLoad` / `hydrate` / `failHistoryLoad`; `startSend` (optimistic pending
user turn, clears a stale popup, returns a temp id) → `completeSend` (reconciles the
user turn, appends the assistant turn with citations / tier / conflict, clears
`sending`, stashes `pendingDisambiguation` when the result carries one) /
`failSend` (marks the user turn failed, clears `sending`, sets `error`);
`removeMessage` (retry); `applyConfirmResult` (append the `chat.confirm_action`
assistant turn, clear the popup); `dismissDisambiguation` (visual-only);
`reset` (`chat.new` — swap `sessionId`, **keep** the transcript so a boundary shows
on the next turn); `clearError`.

**`desktop/src/routes/chatView.ts`** (new; pure) —
`withBoundaries(messages)` interleaves `{ kind: "boundary" }` markers (walks from
index 1, so it structurally cannot emit one before the first turn — covers `chat.new`
*and* a transparent idle auto-close, detected purely from the id);
`shortId` (trailing 6 chars of `session_<hex>`); `formatTimestamp` (locale
date+time, raw string back on a parse failure); `formatCitationLabel`
(`[Session <short> · approx. <ts>]`); `disambigLabel` (action-type → button copy,
raw value fallback).

**`desktop/src/ui/DisambiguationPopup.tsx`** (new; presentational) — a card over the
transcript (not a full-screen modal): one button per backend option via
`disambigLabel`, a × and a backdrop that both dismiss, `role="dialog"`
`aria-modal="true"`, first option focused on mount, `Esc` → dismiss, buttons
disabled while the `chat.confirm_action` round-trip is in flight.

**`desktop/src/routes/Chat.tsx`** (rewrite from the placeholder) —
- **Mount**: `startHistoryLoad()` → `call("chat.history", {})` → `hydrate` /
  `failHistoryLoad`. `.chat-log-loading` while loading (no empty-content flash), an
  empty-state line for a hydrated 0-message session, a retry link on load failure.
- **Unmount cleanup**: `dismissDisambiguation()` — a Tier-2 popup never survives
  leaving `/chat` (visual-only; the backend `_pending_action` self-discards on the
  next `chat.send` / `chat.new`).
- **Transcript**: `withBoundaries(messages).map(...)` — right-aligned user bubbles,
  left-aligned assistant bubbles, a `New conversation` `role="separator"` at each
  boundary, per-message timestamp, a "Retry" button on a `failed` user turn, an
  inline `.chat-conflict` block under an assistant turn that reported a schedule
  overlap ("⚠️ That overlaps with **{title}** ({start}–{end}). Open the Schedule view
  to overwrite or keep it." — no action wired). Tier 3 needs no special case (the
  backend returns it as a normal `answer`).
- **Typing indicator**: while `sending`, a left-aligned `.chat-typing` bubble with an
  animated ellipsis, `role="status"` `aria-live="polite"`; fine for ~120 s.
- **Composer**: full-width `<textarea aria-label="Message">` + a
  `<button aria-label="Send message">`. `Enter` submits, `Shift+Enter` inserts a
  newline. Disabled while `sending` or `phase` is `degraded` / `exited`. Optimistic
  send: `startSend` → `call("chat.send")` → `completeSend` / `failSend`.
- **Auto-scroll**: a bottom sentinel `ref` + `scrollIntoView` keyed on
  `messages.length` / `sending`, suppressed for the one-shot `hydrate()` via a
  `didHydrate` ref (honours `prefers-reduced-motion`).
- **"New conversation"**: `call("chat.new", {})` → `reset(session_id)`; disabled while
  `sending`.
- **Disambiguation**: `<DisambiguationPopup>` when `pendingDisambiguation` is set.
  A choice → `call("chat.confirm_action", { pending_action_id, choice })` →
  `applyConfirmResult`; a `no_pending_action` error → close + "That prompt expired".
  Dismiss (× / backdrop / `Esc`) → close immediately, then
  `chat.confirm_action(..., choice: "conversation")` to append the Tier-4 reply.
- **Degraded/exited**: a `.chat-offline` line + disabled composer (the fe.5 banner
  carries the primary messaging).

**`desktop/src/styles.css`** — a `--- chat interface (3.2) ---` block:
`.chat-view` / `.chat-header` / `.chat-log` (`flex:1; min-height:0` so it scrolls,
not the page) / `.chat-msg[data-role]` / `.chat-typing` / `.chat-boundary` /
`.chat-citation` + `.chat-citation-tip` (CSS popover) / `.chat-conflict` /
`.chat-composer` / `.chat-offline` / `.disambig-*`, plus a
`prefers-reduced-motion` rule for the typing ellipsis.

**No `ipc/` change** — the contract is untouched; the 79-test `ipc` suite and the
existing `chat.send` tier/conflict fixtures already cover the shapes the store
consumes. **No Rust change.**

New tests: `desktop/src/store/session.test.ts` (10) +
`desktop/src/routes/chatView.test.ts` (9). **71 → 90 vitest (desktop).**

#### Verification

`desktop`: `npm typecheck` / `lint` / `test` (90) / `build` green.
`ipc`: 79. Python: `black` / `ruff` / `mypy src observability db` clean;
`pytest` **775 passed, 1 skipped**. Rust: unchanged (7).

E2e via `tauri dev` (model `llama3.1:8b` active, driven over a WebView2
`--remote-debugging-port=9222` CDP seam; `Page.captureScreenshot`):
- lands on `/chat`; empty session hydrated ("Start a conversation…").
- type + `Enter` → optimistic user bubble (with timestamp) → `.chat-typing` →
  assistant reply appended, `sending` cleared.
- `Shift+Enter` keeps the draft and does **not** send.
- "New conversation" → `chat.new` (backend returns a new `session_id`) → the next
  turn renders below a single "New conversation" separator.
- window close → backend exits code 0, zero orphan `python.exe`.
- **Not exercised live** (this box has only ~4 GB free RAM, so `llama3.1:8b` can't
  actually load and every agent call returns the safe `CONVERSATION` fallback):
  the Tier-2 popup, the Tier-3 clarification copy, grounded citations, and the
  inline conflict block — all covered by the store / helper unit tests and the
  `ipc` fixtures.

---

## Step 3.3 — Feature Views (Reminders, Todos, Meetings, Schedule)

The 3.1d **OPEN ITEM** (`ScheduleConflict` surfaced but overwrite/keep resolution
unwired, no overwrite helper on `ScheduleHandler`) is **now closed** — see 3.3a
(`overwrite_ids`) + 3.3e (the inline alert).

Settled decisions built to (Q1/Q2/Q3 + this session's AskUserQuestion):
- **Q1** — feature-CRUD IPC lands as a preliminary backend sub-step 3.3a,
  schema-first, all `worker=True`. Inline NLP create in every view = `chat.send`
  (not a form), **except Meetings** which uses a dedicated `meetings.capture`.

  > **Deliberate deviation from the Q1 pre-planning answer** (confirmed via
  > AskUserQuestion during planning). The Q1 note said "inline NLP create in
  > every view goes through `chat.send`". Meetings capture does **not** — it
  > calls `meetings.capture` (→ `MeetingNoteHandler.capture_meeting_note`)
  > directly. Rationale: a dedicated "Capture" button on a paste-a-transcript
  > text area is a decision the user has *already made explicit* — routing a
  > multi-hundred-line transcript through the four-tier agentic classifier is
  > unreliable, would only avoid a conversational reply at Tier-1
  > `meeting_note`, and the spec requires "no conversational response during
  > capture". The other three views keep `chat.send` (the utterance genuinely
  > is natural language that must be classified). So there are two create paths
  > by design: `chat.send` for reminders/todos/schedule, `meetings.capture` for
  > meetings.
- **Q2** — schedule conflict resolution = `schedule.create_item` with
  `overwrite_ids: list[str] = []`. Non-empty → each id (must be a *current*
  conflict) is soft-deleted, then the item is created, one transaction. Keep =
  dismiss, nothing created. Resolution lives **only** in the Schedule view; no
  chat-side resolution, no `_pending_action` coupling.
- **Q3** — no `reminders.acknowledge_reconciliation`. `reminders.complete/
  dismiss/reschedule` (and `delete`) prune the id from `useReminderStore`
  locally **and** the worker prunes its cached `self._reconciliation`.
- Nav shell = persistent left sidebar. Inline-create Tier-2/3 fallback handled
  inline in the feature view (answer text + option buttons → `chat.confirm_action`).

---

### 3.3a — feature-view CRUD IPC surface (`f6474c5`)

**19 new methods**, all `worker=True` + `degraded_ok=False` (the session DB
connection is thread-affine on `SessionWorker`, same reason `chat.*` /
`health.check` are):

| Namespace | Methods |
|---|---|
| reminders | `list` `complete` `dismiss` `reschedule` `update` `delete` |
| todos | `list` `complete` `update` `delete` |
| meetings | `list` `get` `capture` `delete` |
| schedule | `day` `week` `create_item` `update` `delete` |

- **`src/common/ipc/methods.py`** — `TodoWire` / `ActionItemWire` /
  `MeetingNoteWire` / `ScheduleItemWire` / `ScheduleConflictWire` + params/
  results; `ReminderWire` (from 3.1c) reused verbatim for `reminders.list` /
  the lifecycle results. `ScheduleItemResult = { item | null, conflict | null }`.
- **`src/backend/feature_wire.py`** (new) — entity → wire-dict mappers, sibling
  of `reminders_wire.py`.
- **`src/backend/session_worker.py`** — thin pass-throughs on the worker thread
  (`list_reminders` / `complete_reminder` / … / `create_schedule_item` /
  `schedule_week` / …). Each builds the handler on `self._conn` and returns the
  dataclass(es). `ReminderHandler` is built with `NoOpToastBridge()` — real
  WinRT toast registration is a later step, so create/reschedule via these
  paths do **not** yet register OS toasts (same as `action_dispatch` today).
  `complete/dismiss/reschedule/delete` also call `_prune_reconciliation(id)`
  (Q3). `schedule_week` = one `get_range_schedule` query, grouped into 7 keys.
- **`src/backend/handlers.py`** — 19 thin adapters. `ValueError` →
  `invalid_params`, `KeyError` → `not_found` (via `_feature_call`).
- **`src/features/schedule_handler.py`** — `create_schedule_item` gains
  `overwrite_ids` (Q2): a stray id (not in the current conflict set) →
  `ValueError`; partial coverage → `ScheduleConflict` with only the unresolved
  items; full coverage → soft-delete those rows + insert, one `with self._conn:`
  block (soft-delete only, project_logic §5). New `get_range_schedule(start,
  end)` — one JOIN query backing `schedule.week`. `update_schedule_item` is
  **unchanged** — still returns the conflict, no overwrite path (documented
  limitation; the roadmap conflict-alert acceptance is about *new* items).
- **`ipc/schema/methods.ts` + `ipc/fixtures/methods_examples.json`** — zod
  mirror `.strict()` + one params/result fixture per method (incl. a
  `schedule.create_item` `overwrite_ids` params + a conflict result). The
  cross-language round-trip (`tests/common/test_ipc_methods_roundtrip.py`) and
  `ipc/schema/methods.test.ts` coverage check gate it.

`todos.list` is capped server-side at the newest 500 non-deleted rows (an
internal constant, like `history()`'s 200 — no param).

New/changed tests: `tests/backend/test_ipc_methods_contract.py` (worker set),
`tests/backend/test_handlers.py` (+5, fake worker), `tests/backend/
test_session_worker.py` (+7, real DB + injected worker — pass-throughs +
`_prune_reconciliation` on a seeded overdue reminder + `overwrite_ids` +
`schedule_week` bad-date), `tests/features/test_schedule_handler.py` (+4).
**775 → 866 py** (+91: +16 real, +75 parametrized across the two contract
tests). `black` / `ruff` / `mypy src observability db` clean. **No Rust change.**

---

### 3.3b — nav shell + Reminders view (`ad3d9ad`)

- **`desktop/src/ui/NavRail.tsx`** (new) — persistent left `<nav>`, `NavLink`
  per destination (Chat / Reminders / To-dos / Meetings / Schedule),
  active-link highlight via `aria-current`. Emoji glyphs (no SVG assets).
  Returns `null` on `/` and `/first-run` and while `phase` is `starting` /
  `exited`.
- **`RootLayout.tsx`** — `<NavRail/>` + `<main>` wrapped in a
  `<div class="root-body">` flex row below `<Banner>`.
- **`App.tsx`** — `/reminders` `/todos` `/meetings` `/schedule` routes inside
  the existing `<RequireModel>` wrapper. Todos/Meetings/Schedule shipped as
  "Coming soon" placeholders (filled in by 3.3c/d/e).
- **`desktop/src/routes/featureCreate.ts`** (new, pure) —
  `interpretCreateResult(ChatSendResult)` → `created` | `disambiguation` |
  `conflict` | `message`. The shared inline-NLP-create interpretation for all
  four views. `actionLabel(option)`.
- **`desktop/src/store/reminders.ts`** — extends `useReminderStore` with the
  authoritative `active` list (from `reminders.list`) + `patchReminder`
  (drops a now-completed/dismissed row) / `removeReminder` /
  `pruneReconciliation`. `overdue` / `pendingAcknowledgment` (from the
  `app.reminders_pending` startup event) are **id-set overlays only** — never
  separate rows.
- **`desktop/src/routes/remindersView.ts`** (new, pure) —
  `groupReminders(active, overdueIds, now)` → `{ overdue, today, upcoming }`
  (each reminder once; overdue if its id ∈ `overdueIds` **or**
  `scheduled_time < now`); `datetimeLocalToIso` / `isoToDatetimeLocal` /
  `formatWhen`.
- **`desktop/src/routes/Reminders.tsx`** — grouped list, the Overdue group
  always rendered with a distinct `.reminder-overdue` style (never hidden),
  complete checkbox / delete / reschedule (`datetime-local`), inline NLP create
  (`chat.send` → `interpretCreateResult`) with the Tier-2 option buttons wired
  to `chat.confirm_action`.
- **`desktop/src/ipc/client.ts`** — per-method timeouts for the 19 new methods
  (`meetings.capture` 120 s — one local-LLM pass; the rest 10 s).
- **`styles.css`** — `--- feature views + nav rail (3.3) ---` block.

New tests: `featureCreate.test.ts` (6), `remindersView.test.ts` (6),
`store/reminders.test.ts` (+5). **90 → 107 vitest.**

---

### 3.3c — To-dos view (`5de2234`)

`store/todos.ts` (`useTodoStore` — all non-deleted rows), `routes/todosView.ts`
(`splitTodos` → `{ active, completed }`; active by priority then age, completed
newest-first; `priorityRank` / `priorityLabel`), `routes/Todos.tsx` (priority +
category chips, complete checkbox, inline title edit + priority `<select>` →
`todos.update`, soft-delete, a local "Completed" tab; shared `featureCreate`
inline create). `styles.css` `.todo-tabs` / `.todo-priority[data-level]` /
`.todo-category`. **+9 vitest → 116.**

---

### 3.3d — Meetings view (`53dd07d`)

`store/meetings.ts` (`useMeetingStore` — list + capture lifecycle; a captured
note is prepended), `routes/meetingsView.ts` (`summarizeMeeting` collapsed-row
preview, `actionItemLine`, `formatMeetingDate`), `routes/Meetings.tsx`
(expandable rows — attendees / topics / decisions / action items / follow-ups +
a collapsible transcript, "Review needed" badge when `needs_review`, inline
capture via the dedicated **`meetings.capture`** method — Q1, no `chat.send`, no
conversational response — delete). `styles.css` `.meeting-capture` /
`.needs-review-badge` / `.meeting-detail`. **+9 vitest → 125.**

---

### 3.3e — Schedule view + conflict resolution (`93d560c`)

`store/schedule.ts` (`useScheduleStore` — day = 1 group / week = 7, anchor date,
patch/remove), `routes/scheduleView.ts` (`weekStart` / `addDays` — **UTC**
date-key math so a non-UTC runner/viewer never rolls a day; `layoutDay` →
ordered slots + an overlap flag; `overwriteParams(conflict)` →
`schedule.create_item` params with `overwrite_ids = every conflicting id`),
`routes/Schedule.tsx`:
- Day (default) / Week toggle, prev / next / Today nav, a vertical timeline
  (time gutter + slots; overlapping items flagged red).
- NL edit `<input>` → `chat.send` → shared `featureCreate` helper.
- An **inline** (non-modal) conflict alert with **Overwrite** / **Keep
  existing**, seeded on mount from `useSessionStore.getState().pendingConflict`
  (a conflict raised in the chat view lands here — the only resolution surface)
  **and** from this view's own `chat.send` result. Overwrite →
  `schedule.create_item` with `overwriteParams(conflict)` → refresh + clear both
  the local state and the session-store mirror. Keep → clear both, nothing
  created.
- Per-item delete (`schedule.delete`).

`styles.css` `.schedule-toggle` / `.schedule-nav` / `.schedule-conflict` /
`.schedule-timeline` / `.schedule-slot[data-overlaps]`. **+8 vitest → 133.**

---

### Verification

Per commit: `desktop` `npm typecheck` / `lint` / `test` / `build` green;
`ipc` 136 vitest; Python (3.3a) `black` / `ruff` / `mypy` + `pytest` **866
passed, 1 skipped**. No Rust change.

**E2e** via `tauri dev` (WebView2 `--remote-debugging-port=9222`, CDP driver;
`active_model = llama3.1:8b` in the dev `app_config.json` so the app is past
`<RequireModel>`). The dev `session.db` was seeded with 3 reminders / 3 todos /
2 schedule items / 1 meeting note via the handlers (key from Credential
Manager):
- nav rail renders 5 links; click-navigation switches views; rail hidden on
  `/first-run`.
- **Reminders** — Overdue / Today / Upcoming groups; completing the overdue
  reminder drops it (3 → 2 rows) and the Overdue group disappears (local prune
  + list refresh).
- **To-dos** — Active (2) / Completed (1) tabs; priority + category shown;
  deleting an active todo (2 → 1).
- **Meetings** — the captured note renders; expanding shows the detail
  sections.
- **Schedule** — Day/Week toggle (week = 7 day columns); timeline with the
  seeded items; the NL "apply" field degrades gracefully (this box can't load
  `llama3.1:8b`, so `chat.send` returns the safe `CONVERSATION` fallback and
  nothing is created — no crash).
- **No console errors / warnings** across all five views.

**Not exercised live** (needs a Tier-1 agentic classification, which needs a
loaded model): inline NLP create actually creating an entity, the Tier-2
option-button path, and the Schedule conflict Overwrite/Keep flow. Covered by
`featureCreate.test.ts` + `scheduleView.test.ts` + `tests/features/
test_schedule_handler.py` (`overwrite_ids`) + `tests/backend/
test_session_worker.py` + the `ipc` `schedule.create_item` conflict fixture.

---

## Step 3.4 — Settings & Diagnostics

Settled decisions built to (pre-planning Q1/Q2/Q3 + this session's AskUserQuestion):
- **Q1** — General settings persist as `AppConfig` fields in
  `data/app_config.json` (`idle_timeout_minutes` / `summary_time`), **not** a
  `settings.json`, **not** a SQLite table. `_CONFIG_VERSION` 2 → 3 (v2 files load
  fine). `settings.get` returns the *effective* value (config → env → YAML →
  default) + a `*_is_default` flag per field; `settings.update` writes `AppConfig`.
- **Q2** — "Export all data" uses `@tauri-apps/plugin-dialog` `save()` for the
  path, then `data.export` → `DataManager.write_export(path)`.
- **Q3** — "Report a problem" = generate a redacted log → `plugin-dialog`
  `save()` → `revealItemInDir` → open a `mailto:` compose window whose body tells
  the user to attach the file (`mailto` cannot attach — documented limitation).
- Settings = **one `/settings` route**, active panel from a `?tab=` URL param.
- The rotating log file + redaction is **its own backend sub-step (3.4a)**.
- `diagnostics.metrics` aggregates **only what `metrics.db` records today**;
  error rate / compute time render "not tracked yet (v1.1)".

---

### 3.4a — rotating backend log file + redaction (`84414b5`)

The backend only logged to stderr (`logging.basicConfig`), which the Tauri shell
inherits and a packaged app drops. `diagnostics.logs` / `diagnostics.report` need
a persisted log.

- **`src/backend/paths.py`** — `log_dir()` (`<data_dir>/logs`) + `log_file()`
  (`backend.log`), one definition shared by the handler and the diagnostics
  handlers.
- **`src/backend/logging_setup.py`** — `configure_logging()`: keeps the stderr
  `StreamHandler` (format `%(asctime)s %(levelname)s %(name)s %(message)s`) and
  adds a `RotatingFileHandler` at `paths.log_file()` (1 MB × 3, `delay=True`).
  Safe to call repeatedly — prior handlers it installed are removed first, so a
  test that runs `main()` under a fresh `RAGPIPE_DATA_DIR` re-points rather than
  stacking. A read-only data dir logs a warning, doesn't crash.
- **`src/backend/log_redaction.py`** — `redact(text)` scrubs e-mail addresses,
  `C:\Users\<name>` / POSIX home dirs, `authorization|bearer|api_key|secret|
  password` assignments (rest of line), 32+-char hex runs, IPv4.
  `write_redacted_report(dest)` concatenates `backend.log` + its rotations
  oldest-first, redacts, writes atomically. **Redaction is always backend-side**
  — applied by `diagnostics.logs` per entry *and* by `diagnostics.report`; the
  frontend never redacts.
- **`src/backend/main.py`** — `main()` calls `configure_logging()` before serving.

Tests: `test_logging_setup.py` (5), `test_log_redaction.py` (9).

---

### 3.4b — settings persistence + settings / data / diagnostics IPC (`a823891`)

Schema-first, mirrors 3.3a: Pydantic contract → zod mirror → fixtures → dispatcher
handlers → worker pass-throughs where the session DB is touched → round-trip test.

**8 new methods:**

| Method | worker | degraded_ok | Params → Result |
|---|---|---|---|
| `settings.get` | no | yes | `{}` → `{idle_timeout_minutes, summary_time, idle_timeout_is_default, summary_time_is_default}` |
| `settings.update` | no | yes | `{idle_timeout_minutes?, summary_time?}` (present=write, `null`=clear, absent=unchanged via `model_fields_set`) → same as `settings.get` |
| `data.info` | no | yes | `{}` → export-badge line + `days_since` + the 3 Data & Privacy copy strings |
| `data.export` | **yes** | no | `{path}` → `{path, exported_at, bytes_written}` |
| `data.wipe` | **yes** | no | `{confirm}` (must be `true`) → `{wiped}` |
| `diagnostics.logs` | no | yes | `{level?, limit=200}` → `{entries[], truncated}` (redacted) |
| `diagnostics.metrics` | no | yes | `{limit=1000}` → aggregate (below) |
| `diagnostics.report` | no | yes | `{path}` → `{path, bytes_written}` |

`diagnostics.metrics` — `MetricsStore.query_recent(limit, env="backend")`
aggregated in Python: `sample_size`, `retrieval_latency_ms {p50,p95,p99}` (from
the only latency the chat spine records), `confidence_distribution
{high,medium,low,none}`, `grounded_rate`, `retrieval_hit_rate`. `error_rate` and
`compute_ms` are `null` — not instrumented yet, a documented v1.1 gap.

**Settings persistence (Q1):**
- `AppConfig` gains `idle_timeout_minutes: int | None` + `summary_time: str | None`;
  `_CONFIG_VERSION` 2 → 3; `set_idle_timeout_minutes` / `set_summary_time` (accept
  `None` to clear).
- **`src/backend/settings.py`** — the single resolver: `effective_idle_minutes`
  (AppConfig → `$RAGPIPE_SESSION_IDLE_MINUTES` → 45), `env_yaml_summary_time`
  (`$RAGPIPE_SUMMARY_DAILY_TIME` → YAML → 21:00), `effective_summary_time`
  (AppConfig on top), and **`resolved(cfg)`** which computes the whole
  `settings.get` result **from one `AppConfig`** (+ env/YAML) so the value and its
  `*_is_default` flag can never skew and `settings.update` answers from the
  object it just saved.
- **Live, no restart:** `SessionWorker.send()` reads
  `backend_settings.effective_idle_minutes(self._app_config_path)` **per call**
  (the module `_idle_minutes()` / `_DEFAULT_IDLE_MINUTES` are gone; a test can
  still pin `idle_minutes=`). `SchedulerThread._tick()` refreshes
  `self._summaries.config.daily_time` from `AppConfig.load().summary_time or
  SummaryConfig.from_yaml().daily_time` each poll.
  - *Deviation from the Q1 wording:* Q1 said "`SummaryConfig` gains an `AppConfig`
    read ahead of the env/YAML lookup". `SummaryConfig.from_yaml()` is left
    unchanged (env → YAML → default — its many callers/tests untouched); the
    AppConfig layer is applied by the scheduler tick and by
    `settings.effective_summary_time`. Same effect (a settings edit takes effect
    with no restart), narrower blast radius.

**`data.*` on the worker:** new `SessionWorker.export_data(path)` /
`wipe_data()` build a `DataManager` on `self._conn`. **After `full_wipe()` the
worker resets** `_session_id` / `_turn_count` / `_pending_action` /
`_reconciliation` and, in a real (non-injected) run, rebuilds the vector index +
router + model bundle so retrieval no longer surfaces the wiped chunks.
**Toast cancellation on wipe is a documented no-op** — `full_wipe` calls
`NoOpToastBridge.cancel_all()`; toast *registration* is also a no-op today (no OS
toast is ever scheduled yet). When the real WinRT `ToastBridge` step lands its
`cancel_all()` (per-id `RemoveFromSchedule`) is inherited here for free. Carried
forward.

**`data.info`:** `export_badge_state(app_config, now)` was lifted out of
`DataManager` into a module function (the method delegates) so the handler builds
the badge from `AppConfig` alone — no DB connection.

Tests: `test_settings.py` (7), `test_diagnostics.py` (8), `test_handlers.py`
(+11), `test_ipc_methods_contract.py` (degraded / worker sets), `test_app_config.py`
(v2→v3, general-settings roundtrip), `test_session_worker.py` (+3: idle-from-config
live, `export_data`, `wipe_data`), `test_data_admin.py` (+1 standalone badge),
`ipc/schema/methods.test.ts` + `tests/common/test_ipc_methods_roundtrip.py` (+16
fixtures).

---

### AppConfig concurrency hardening (follow-up fix — found via the 3.4b e2e)

The in-process end-to-end (`main()` fed `settings.get` / `settings.update` /
`data.info` / `diagnostics.*` envelopes) surfaced two real races in
`data/app_config.json`, both new pressure from 3.4b (`settings.update` +
`SchedulerThread` reading the file per tick):

1. **Windows sharing violation** — a reader (`SessionWorker.send()` per message,
   `SchedulerThread._tick()` per poll) holds a read handle without
   `FILE_SHARE_DELETE`, so a concurrent `os.replace` fails with `WinError 5`; and
   a read during the writer's `os.replace` hits a sharing violation.
   → `AppConfig.load()` and `save()` retry read / rename a few times
   (~155 ms total) before giving up.
2. **Lost update** — two in-flight `settings.update` calls each did
   `load → mutate → save` unsynchronised, so one clobbered a field it hadn't
   touched.
   → `_WRITE_LOCK` is now an `RLock`; new `AppConfig.update_fields(path, **changes)`
   holds it across the whole load-modify-save; `settings.update` uses it. `save()`
   also writes to a **per-thread-unique** temp name.

Neither is Phase-3.4-specific in principle (`settings.update` vs `send()` reading
`active_model` already raced), so the hardening lives in `AppConfig` itself.

---

### 3.4c — Settings shell + nav + export badge + General & Models (`12a0a1c`)

- **`App.tsx`** — `/settings` behind `<RequireModel>`.
- **`NavRail.tsx`** — Settings entry + a `●` dot when `needsExportBadge`
  (`lastExportedAt` null or > 30 days, mirrors `data_admin._EXPORT_BADGE_DAYS`).
- **`store/settings.ts`** — `lastExportedAt`, one source of truth: `AppConfig` →
  `app.status.last_exported_at` → this store (set in `bootstrap.ts`) → the nav
  dot; re-read after export / wipe.
- **`routes/settingsView.ts`** (pure, tested) — `SETTINGS_TABS` + `activeTab(?tab=)`,
  `needsExportBadge`, `isValidIdle` / `isValidTime`.
- **`Settings.tsx`** — one route, `useSearchParams` for the active panel.
- **`SettingsGeneral.tsx`** — idle timeout + summary time forms over
  `settings.get` / `settings.update`; per-field "Use default" (sends `null`);
  effective-value hints.
- **`ui/ModelCatalogList.tsx`** — the catalog-row list **extracted from
  `FirstRun.tsx`** (rows / Download / Activate|Switch / progress), driven by
  `useModelStore` so `/first-run` and Settings → Models stay in sync. `FirstRun`
  now consumes it (no behaviour change — its E2E path + `modelRow` tests
  unchanged). `SettingsModels.tsx` adds the `"Switch"` variant, no navigate-away.
- **`client.ts`** — per-method timeouts for the 8 new methods.

Tests: `settingsView.test.ts` (9), `store/settings.test.ts` (1). **133 → 143 vitest.**

---

### 3.4d — Data & Privacy + Backup & Recovery (`5bbc649`)

Folds in the Phase-2-deferred export badge / full-wipe confirm / restore UI.

- **`@tauri-apps/plugin-dialog`** — `Cargo.toml` + `lib.rs` +
  `capabilities/default.json` (`"dialog:allow-save"`) + npm dep. The native save
  picker.
- **`ui/ConfirmDialog.tsx`** — `role="alertdialog"` modal for the irreversible
  actions (Escape / backdrop cancel, focus to the confirm button).
- **`SettingsData.tsx`** — `data.info` copy strings (backend-authoritative);
  "Export all data" → `save({defaultPath: mirrormind-export-<date>.json})` →
  `data.export` → refresh the badge; "Delete all my data" → `ConfirmDialog` →
  `data.wipe` → `window.location.reload()` so every store re-hydrates from a
  clean `app.status`.
- **`SettingsBackup.tsx`** — `backup.list` rows → `ConfirmDialog` →
  `backup.restore`. The handler is inline and sends its `ok` response *before*
  the serve loop breaks, so `call()` normally resolves; but the process exit can
  race the response read, so a **`backend_exited` transport error is treated as
  "restart underway"**, never a failure. The fe.5 "Applying your backup…" banner
  (already wired via `app.restore_staged` + `backend:exit {will_retry}`) takes
  over.

`cargo fmt` / `clippy -D warnings` / `test` (7); desktop typecheck / lint / test
(143) / build.

---

### 3.4e — Diagnostics + Report a problem (`0a6ab4a`)

- **`@tauri-apps/plugin-opener`** — `Cargo.toml` + `lib.rs` + scoped
  capabilities (`opener:allow-reveal-item-in-dir` path `**`,
  `opener:allow-open-url` `mailto:*`) + npm dep.
- **`routes/diagnosticsView.ts`** (pure, tested) — `levelParam`, `barWidths`,
  `formatPct` / `formatMs` ("not tracked yet" for `null`), `reportMailto` (the
  pre-filled body that names the saved file and tells the user to attach it).
- **`SettingsDiagnostics.tsx`**:
  - *Recent activity* — `diagnostics.metrics` → retrieval-latency p50/p95/p99,
    a confidence-distribution bar row, grounded / hit rates; error rate + compute
    time show "not tracked yet".
  - *Logs* — level `<select>` (ALL/DEBUG/INFO/WARNING/ERROR) + Refresh →
    `diagnostics.logs` (redacted), newest 200, truncation note. Colour by level.
  - *Report a problem* — `save()` → `diagnostics.report` → `revealItemInDir` +
    `openUrl(mailto)`. Both opener calls are best-effort (`.catch`) so a scope
    miss never breaks the saved report.

Tests: `diagnosticsView.test.ts` (6). **143 → 149 vitest.** `cargo` clean (7).

---

### Verification

Per commit: `black` / `ruff` / `mypy src observability db` clean; desktop
`typecheck` / `lint` / `test` / `build`; `cargo fmt` / `clippy --all-targets -D
warnings` / `test` for the plugin sub-steps; `ipc` vitest (**136 → 160**);
`tests/common/test_ipc_methods_roundtrip.py` **82 → 98** (Pydantic↔zod per fixture,
real Node subprocess).

**In-process end-to-end** (`src.backend.main` fed request envelopes over
BytesIO, no model, exit 0):
- `settings.get` → effective defaults (45 / 21:00, both `is_default`).
- `settings.update {idle:30, summary:"07:15"}` → both set, `is_default` false;
  a follow-up `{idle:null}` clears **only** idle (summary stays "07:15") — the
  lost-update fix.
- `data.info` → the real `NEVER_EXPORTED_LINE` / `UNINSTALL_WARNING` /
  `EXPORT_BLURB` strings.
- `diagnostics.logs` → parsed, redacted `backend.log` entries.
- `diagnostics.metrics` → `sample_size: 0` on a fresh DB, all buckets 0,
  `error_rate` / `compute_ms` null.

**Not exercised live** (needs `tauri dev` + a WebView2 CDP session, deferred to
the Phase 3 audit): the native save dialog, `window.location.reload` after wipe,
the `backup.restore` → exit 5 → supervisor swap round-trip, `revealItemInDir` /
`openUrl(mailto)` scope behaviour. Covered by unit tests + the ipc fixtures + the
in-process e2e above; the restore path is unchanged from fe.7's verified flow.

