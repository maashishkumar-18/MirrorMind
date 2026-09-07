# Phase 3 Step 3.1 — Frontend Architecture and IPC Client

**Status:** sub-steps **3.1a** + **3.1b** landed. 3.1c–3.1d + the Rust/React scaffold are
pending.

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
| NL slot extraction (utterance → reminder/todo/schedule/meeting_note fields) + `AgenticOutput.action_type` → feature-handler dispatch (deferred from Step 1.5; `chat.send` returns `action_type` but does not act on it) | Python backend | **3.1d** |
| **Per-message orchestrator** (`chat.send`, project_logic §4): persist user msg → `RetrievalAgent.reason` → `RetrievalRouter.route` → `GenerationOrchestrator.generate` (short-circuit to `ao.response` when `retrieve_needed=False`) → persist assistant msg → trigger ingestion | Python backend | **3.1b ✅** |
| `sessions`/`messages` persistence (`SessionRepository(TableHandler)`), `chat.new` / `chat.history`, in-memory session state on the `SessionWorker` thread | Python backend | **3.1b ✅** |
| generation routed through the app-config active model (gate: `model_setup_required()` → `no_model_active`; `RetrievalAgent(model=)` / `MetadataExtractor(model=)` / `GenerationOrchestrator(model_name=)`) | Python backend | **3.1b ✅** |
| `SQLiteVectorStore` key wiring — the `SessionWorker` opens `SQLiteVectorStore(db_path, key=key)` on its own thread; `SQLiteVectorStore.close()` added; `coordinator.register("session_worker", …)` teardown (scheduler → session_worker → tracing) | Python backend | **3.1b ✅** |
| idle-session auto-close (§13): lazy check at `chat.send` entry + `SchedulerThread` backstop + on-launch `ReminderHandler.reconcile_on_launch()` | Python backend | **3.1c** |
| `src-tauri/` Rust shell, `tauri.conf.json` (**`bundle.externalBin` for the Python sidecar** + `shell:sidecar` allowlist — a missing `externalBin` is the classic "works in dev, broken in MSIX"), Vite + React + TS + Zustand + React Router, typed **zod IPC client**, `ipc/schema/methods.ts` mirror + round-trip test | Rust + React | **scaffold sub-step** |
| Degraded-mode **banner UI** ("AI features temporarily unavailable — restarting…" → "Ready"), first-launch model **flow UI**, version-mismatch → "please restart" UI, IPC timeout → "temporarily unavailable" | React frontend | **scaffold sub-step** |
| Tauri **process supervisor** — restart with exponential backoff 1s/2s/4s, **3 restarts after the initial launch (4 total launches)**, then degraded banner + "Restart app" | Rust shell | **later** |
| **Restore file swap** — Rust `fs::rename` of `validated_snapshot_path` with the backend fully down, then relaunch (pairs with 3.1a's `stage_restore` + exit code 5; `os.replace` onto the live DB cannot succeed on Windows with connections open — resolves `PHASE_2_AUDIT.md` 2.3-C1) | Rust shell | **later** |
| Single-instance **enforcement** + bring-first-window-to-foreground | Rust shell | **later** |
| Real **WinRT `ToastBridge`** — `cancel_all()` must iterate `RemoveFromSchedule` **per id** (Windows has no bulk-cancel API); the notifier that removes a scheduled toast must be the **same `ToastNotifier` instance** that scheduled it | Rust (called from Python via IPC) | **later** |
| Subprocess-kill **fuzzing test** — 100 iterations on `windows-latest`, kills during **IPC message processing**, **DB write**, and **Ollama inference** (not just idle) → assert detect → restart → banner → recover, zero data loss | Rust + test runner | **later (roadmap Step 4.4)** |
| Step 2.4 **accessibility** (WCAG 2.1 AA / axe-core / Narrator / string externalization) | React frontend | **later Phase 3** |
| PyInstaller `.spec` + freeze verification (sqlcipher3 / keyring / sentence-transformers) | packaging | **Phase 5** |

**One restart *sequence*, two triggers** (`PHASE_2_AUDIT.md` hand-off 2): a backend **crash** →
supervisor relaunches (backoff). A **restore** → supervisor stops the backend (quiescing every
DB connection), *then* `fs::rename`, *then* relaunches. 3.1a delivers the backend half: exit
code 5 + an `app.restore_staged` event carrying `validated_snapshot_path`.

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
