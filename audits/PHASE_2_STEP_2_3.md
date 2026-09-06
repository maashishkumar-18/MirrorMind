# Phase 2 Step 2.3 — Crash Recovery and Process Supervision

**Commit:** _(this step)_ · **Prereqs:** 2.1 `009d132` (encryption, `check_integrity`),
2.2 `a9cfa1f` (backup/restore, `RestoreResult.needs_restart`).

Roadmap Step 2.3 is overwhelmingly Tauri/Rust work that cannot exist until `src-tauri/`
does. This document is the authoritative split between what the Python backend owns (landed
in this step) and what Phase 3 owns, plus the backend crash-safety audit that "crash
recovery" concretely means for a single-file SQLite backend.

---

## Section 1 — Scope split

| Concern | Owner | Status |
|---|---|---|
| Process supervisor: restart w/ exponential backoff (1s, 2s, 4s), 3 attempts, then degraded-mode banner + "Restart app" | Tauri Rust shell | **Phase 3** |
| Single-instance **enforcement** + bring first instance's window to foreground (named event / WM_COPYDATA) | Tauri Rust shell | **Phase 3** |
| Degraded-mode banner; every IPC timeout → "temporarily unavailable" UI state, never an infinite spinner | React frontend | **Phase 3** |
| Per-IPC-call explicit timeouts (frontend → backend transport) | Typed IPC client (Phase 3 Step 3.1) | **Phase 3** |
| Subprocess-kill fuzzing test (kill the backend at random points, assert detect → restart → banner → recover) on `windows-latest` | Tauri + a test runner | **Phase 3** |
| **Clean shutdown when the stop signal arrives** — scheduler joined, traces flushed, connections closed, in order | Python backend | **2.3 ✅** |
| **Every outbound HTTP call bounded by an explicit `timeout`** | Python backend | **2.3 ✅** (audit + CI lock) |
| **Single-instance guard for the frozen `backend.exe`** (defense-in-depth; Rust owns canonical enforcement) | Python backend | **2.3 ✅** |
| **Crash-safe write discipline on every backend write path** | Python backend | **2.3 ✅** (audit + 1 fix) |

**One restart path, not two.** `RestoreResult.needs_restart` (2.2) and a crash both resolve
to the same Phase 3 action: the supervisor stops the backend, then relaunches it. The
backend's job on the way down is `ShutdownCoordinator.shutdown()`; on the way up it is the
2.1 startup sequence (resolve key → migrate → `check_integrity` → offer restore).

### What landed

- `src/backend/lifecycle.py::ShutdownCoordinator` — register `(name, close)` pairs; teardown
  in reverse order; each step guarded (a raiser is logged, the rest still run); idempotent.
  No signal handlers, no hard per-step timeout — those are Phase 3 `main()`'s job, and each
  `close` callable is expected to be self-bounded (`SchedulerThread.stop(timeout=)`,
  `shutdown_tracing()` over Langfuse's own bounded `shutdown()`).
- `src/backend/single_instance.py::SingleInstanceGuard` — Win32 `CreateMutexW` on
  `Global\PersonalAICompanion_v1_SingleInstance`; `acquire()` is `False` when
  `GetLastError() == ERROR_ALREADY_EXISTS`. No-op returning `True` off Windows.
- `observability/tracing.py::shutdown_tracing()` — `client.shutdown()` (flush + join worker),
  guarded, no-op when tracing disabled.
- `tests/backend/test_http_timeouts.py` — AST invariant: no `requests.<method>(...)` call
  anywhere under `src/` without a `timeout=` kwarg.

---

## Section 2 — Backend write-path crash-safety audit

Every path that mutates persistent state, its atomicity mechanism, and what a mid-write
crash leaves behind.

| Write path | Mechanism | On mid-write crash |
|---|---|---|
| Session DB — schema migrations (`db/migration_runner.py`) | Explicit `BEGIN` / `COMMIT` / `ROLLBACK` on an `isolation_level=None` connection; the `schema_migrations` bookkeeping insert is in the same transaction ([[sqlite3-ddl-not-atomic-in-with-conn]]) | SQLite + WAL discards the incomplete transaction on next open. `schema_migrations` correctly shows the migration as *not applied*; a re-run retries cleanly. A pre-migration snapshot was already taken. |
| Session DB — handler DML (`src/features/*_handler.py`, `sqlite_vector_store.py`, `data_admin.full_wipe`) | `conn.commit()` per statement, or `with conn:` per multi-statement unit; WAL journal mode on every connection (`db/connection.py`) | The uncommitted transaction is rolled back on next open (WAL). `PRAGMA integrity_check` (2.1, `db/health.py`) is the on-launch guard; restore-from-backup (2.2) is the recovery path if the file is genuinely damaged. |
| `data/app_config.json` (`src/models/app_config.py::save`) | Write `*.tmp`, then `os.replace` onto the target | Either the old file is intact or the new file is intact — `os.replace` is atomic on Windows and POSIX. A leftover `*.tmp` is harmless and overwritten next save. |
| Daily backup file (`src/features/backup_manager.py::create_backup`) | **Was:** `source.backup(dest)` straight to `session-<ts>.db`. **Now (fixed this step):** back up to `session-<ts>.db.partial`, then `os.replace` to the final name. | A crash mid-copy leaves an orphaned `*.db.partial`. `list_backups()` globs `session-*.db` and ignores it; `prune()` and `restore()` never see it. `restore()` also independently runs `check_integrity` before swapping. |
| Restore (`src/features/backup_manager.py::restore`) | Validate snapshot (keyed open + `check_integrity`, abort untouched on failure), copy to `<db>.incoming`, `os.replace` onto the live path, `unlink(missing_ok=True)` the `-wal`/`-shm` sidecars | The live DB is either fully intact (validation or copy failed before `os.replace`) or fully replaced. Never a half-restored file. |
| Pre-migration snapshot (`db/migration_runner.py::snapshot`) | `source.backup(dest)` to a fresh `<stem>-pre-migration-<ts>.bak` | A partial `.bak` is orphaned and never consumed — snapshots are best-effort and the migration re-runs safely regardless. Low priority; not changed. |
| `observability/metrics.db` (`observability/metrics_store.py`) | Separate database, WAL mode, a fresh connection opened and closed per `record()` call — no persistent handle | Independent of the session DB. Telemetry loss on crash is acceptable; the session DB is unaffected. |

### Section 3 — The one gap found, and its fix

`BackupManager.create_backup` wrote the online backup directly to its final
`session-<ts>.db` name. A crash (or a disk-full) partway through `source.backup(dest)` would
leave a truncated `.db` that `list_backups()` would surface and a user could select for
restore. Fixed by writing to `session-<ts>.db.partial` and `os.replace`-ing to the final
name only once the copy completes. Covered by
`tests/features/test_backup_manager.py::test_create_backup_leaves_no_partial_file` and
`::test_list_backups_ignores_a_stray_partial_file`.

No other gaps found. WAL mode + the explicit-transaction discipline from Phase 0 mean the
session database needs no application-level write-ahead log or in-flight-transaction
recovery of its own — SQLite provides both.
