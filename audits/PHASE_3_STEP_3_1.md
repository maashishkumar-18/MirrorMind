# Phase 3 Step 3.1 — Frontend Architecture and IPC Client

**Status:** sub-step **3.1a** landed. 3.1b–3.1d + the Rust/React scaffold are pending.

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
| **Per-message orchestrator** (`chat.send`, project_logic §4): persist user msg → `RetrievalAgent.reason` → `RetrievalRouter.route` → feature-handler dispatch → `GenerationOrchestrator.generate` via `ModelInferenceRouter` → persist assistant msg → trigger ingestion | Python backend | **3.1b** |
| `sessions`/`messages` persistence, `chat.new`, idle-session close (§13), on-launch `ReminderHandler.reconcile_on_launch()` | Python backend | **3.1b / 3.1c** |
| `SQLiteVectorStore` key wiring as a named `main()` composition step (`db_path + key`, own connection, `coordinator.register("vector_store", store.close)` between `session_db` and `scheduler`) | Python backend | **3.1b** |
| NL slot extraction (utterance → reminder/todo/schedule/meeting_note fields) + `AgenticOutput` → handler dispatch (deferred from Step 1.5) | Python backend | **3.1d** |
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

## What landed in 3.1a (commit — this step)

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
