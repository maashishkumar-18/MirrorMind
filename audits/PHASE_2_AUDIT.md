# Phase 2 Comprehensive Audit — Non-Functional Hardening

**Scope:** All of Phase 2 (Steps 2.1–2.3) of the Personal AI Companion migration, per
`production_roadmap.md` — SQLCipher encryption + key management, backup/restore/export/wipe,
and the crash-recovery / process-supervision backend slice. Commits `009d132` → `a9cfa1f` →
`fde28c1` (3 commits, one per step, on `main`).

**Method:** Three independent audit agents (one per step, fresh context, no access to the
implementation's own reasoning) re-verified code, tests, and the roadmap acceptance criteria
against the live repository — creating and corrupting real encrypted databases, driving the
restore path with a still-open connection, spawning a real cross-process named-mutex holder,
adding a deliberately-unbounded HTTP call to confirm the CI guard actually fails, re-deriving
retention/idempotency math, and reproducing every claimed failure mode. A fourth cross-cutting
pass (git history, secret scan, dependency resolution, lint/type-suppression scope, CI
coverage, `.env` / `.gitignore` consistency) was done directly. Every finding below was
reproduced before being accepted.

**Outcome:** All three steps meet their spec and acceptance criteria. **No HIGH-severity
issue.** The core security and data-recovery guarantees hold under adversarial testing: the
at-rest file is fully encrypted (header included), a wrong key or corruption surfaces as a
typed error rather than a crash or a silent "ok", the exact Windows-reinstall message is
byte-exact and the old file is never touched, DDL atomicity survives the cipher-driver swap,
backups and their snapshots are themselves encrypted, and restore of a corrupt snapshot leaves
the live database untouched.

**Two MEDIUM** findings, both fixed in the remediation commit: `BackupManager.restore()` raised
an uncaught `PermissionError` (instead of `RestoreResult(ok=False)`) when the live DB was still
open, and the pre-migration `.bak` carried the identical partial-file crash risk that Step 2.3
had just fixed for daily backups. The rest were LOW — an unvalidated encryption key that was
string-formatted into a PRAGMA, a non-idempotent `shutdown_tracing()`, a UTC/local date
mismatch in the once-per-day backup guard, orphaned temp files on mid-write errors, a
missing coordinator lock, and doc/scope-accuracy items. The clear ones were fixed with
regression tests; the judgment calls and Phase-3 hand-offs are documented below.

**A pre-existing test-quality defect was found and fixed:** all four encryption-test key
constants were **65 hex characters**, not 64. `sqlcipher3` silently tolerated the extra
nibble, so the tests were not exercising the real 64-hex `resolve_key()` contract. The new
key-format validation caught this; the constants were corrected.

The test suite is green on `main` after remediation (**581 → 596**, +15 regression tests, 1
skip = non-Windows single-instance fallback); `black --check`, `ruff check`, and
`mypy src observability db` are all clean.

---

## How to read this document

- **PASS** — verified correct, no action needed.
- **CONCERN** — technically correct but fragile, imprecise, or worth improving later.
- **FINDING** — a real bug, gap, or inaccuracy. Each tagged `[FIXED]` or `[DOCUMENTED]`.

Severity: **HIGH** (undermines a core security / data-integrity guarantee) · **MEDIUM** (real
gap, contained blast radius) · **LOW** (cosmetic / doc accuracy / defense-in-depth, no
functional impact today).

---

## Cross-Cutting Checks (repo-wide)

**PASS** — Git history: 3 Phase 2 commits, linear on `main`, one per step, each message
accurate against `--stat` (2.1 `+762/-39`, 2.2 `+867/-2`, 2.3 `+551/-1`). No overclaiming.
Working tree clean.

**PASS** — Secret / artifact scan: no key material, credential file, `.db`, or `.bak`
committed. Key handling is correct end to end — `os.urandom(32).hex()` → `keyring.set_password`;
the key is never logged, never written to a file, never returned in an error payload. The one
place it touches SQL text (`PRAGMA key`) is now format-validated (see 2.1-F1).

**PASS** — `.gitignore`: the root-anchored `/data/` rule covers `data/backups/` (confirmed
with `git check-ignore`); `db/*.db*` + `db/**/*.bak` cover the session DB and migration
snapshots. `git ls-files` shows nothing committed from those paths.

**PASS** — Dependency resolution: `sqlcipher3==0.6.2` ships a real `cp311-cp311-win_amd64`
wheel with a statically-linked libsqlcipher 4.12 (FTS5 + JSON1 compiled in); `keyring==25.6.0`
pulls `pywin32-ctypes 0.2.3` on win32. `pip check` is clean — no conflict introduced. Both are
in `requirements.txt` (prod); `requirements-dev.txt` inherits them via `-r`. The ubuntu `eval`
CI job (workflow_dispatch-only) gets the Linux `sqlcipher3` wheel; no Phase 2 code calls
`keyring` outside tests, and the tests always inject an in-memory fake, so a headless Linux
keyring backend is never exercised.

**PASS** — Lint / type suppression scope: the `[[tool.mypy.overrides]] ignore_errors` list is
**unchanged** by Phase 2 — no new module is type-suppressed. `mypy src observability db` covers
`src/backend/`, `src/security/`, `db/health.py`, `src/features/backup_manager.py`, and
`src/features/data_admin.py` at full strength and passes. No new `ruff` per-file-ignore was
added; the new code passes the full `E, F, I, UP, B` selection.

**PASS** — CI: `lint-python` and `test` both run on `windows-latest` and pick up
`tests/backend/`, `tests/security/`, `tests/common/db/`, and `tests/observability/`
automatically — no new job needed. The runner has a real `WinVaultKeyring` and the win_amd64
`sqlcipher3` wheel; the `SingleInstanceGuard` win32 path is exercised for real.

**PASS** — `.env.example`: updated coherently — states the DB key lives in the Credential
Manager, not an env var, and adds the `RAGPIPE_BACKUP_*` overrides.

**CONCERN (LOW)** — The roadmap's literal **"Phase 2 Milestone"** ("Encryption at rest.
Backup and restore operational. **Crash recovery tested by fuzzing. Accessibility verified.
Performance benchmarked against reference hardware.**") is **not fully met**, and nothing in
the repo says so plainly. The first two clauses are delivered and verified. The last three are
deferred: subprocess-kill fuzzing needs the Phase 3 Tauri shell; accessibility (Step 2.4) is
entirely Phase 3 frontend; performance benchmarking (Step 2.5) is deferred to pre-Phase-5
because it must run on the §9.1 reference hardware. This document is the record of that split.

---

## Step 2.1 — SQLCipher Encryption and Key Management (`009d132`)

Independent verification created encrypted databases, corrupted their pages, opened them with
wrong keys, ran migrations under cipher with a deliberately-broken `0002`, and drove
`resolve_key` through all three launch states. **All 9 acceptance criteria PASS.**

**PASS** — The at-rest file is fully encrypted, header included (`cipher_plaintext_header_size`
defaults to 0 in SQLCipher 4). A seeded canary string is absent from the raw bytes;
`sqlite3.connect()` + a read raises `DatabaseError("file is not a database")`.

**PASS** — Key generation is exactly `os.urandom(32).hex()` → 64 lowercase hex → 32 bytes.
`keyring` round-trips through an injected in-memory backend; the `fake_keyring` fixture saves
and restores the real backend; no `keyring` usage exists anywhere in the test tree outside
`tests/security/`.

**PASS** — Key-not-found handling is exact. No key + no DB file → generate + persist. No key +
DB file present → `PreviousDataUnrecoverableError`, whose `str()` is **character-exact** to the
roadmap string, raised **without opening or reading the old file** (`resolve_key` calls
`load_key()` then `Path.exists()`, nothing more).

**PASS** — Integrity detection: `check_integrity` / `check_quick` return `ok=True` on a fresh
migrated DB (plaintext and encrypted); on a plaintext DB with a zeroed interior page →
`ok=False` with detail rows; on a bit-flipped **encrypted** interior page → the pragma raises a
driver error which `_run_check` catches via `database_errors()` and reports as `ok=False` — it
does **not** silently return ok.

**PASS** — Migrations under cipher: `0001` (all 4 FTS5 virtual tables) applies on a fresh
encrypted DB; a broken `0002` in a private copy of the migrations dir raises and leaves
**nothing** partial — `should_not_survive` absent from `sqlite_master`, `0002` absent from
`schema_migrations`. The explicit `BEGIN`/`COMMIT`/`ROLLBACK` on `isolation_level=None`
behaves identically under `sqlcipher3.dbapi2` ([[sqlite3-ddl-not-atomic-in-with-conn]]).

**PASS** — The pre-migration `.bak` is itself encrypted (both `snapshot()` connections keyed).

**PASS** — `key=None` is byte-for-byte the pre-Phase-2 plaintext path. The two driver
exception trees are genuinely disjoint (`issubclass` is `False` both ways), so
`operational_errors()` / `database_errors()` are load-bearing; `sqlite3.Row` really does raise
`TypeError` on a `sqlcipher3` cursor, so `set_session_row_factory` is load-bearing. No
production code catches a bare `sqlite3.*Error` on a path that can carry a cipher connection.

### FINDING 2.1-F1 — LOW — unvalidated key string-formatted into `PRAGMA key` — `[FIXED]`

`_KEY_PRAGMA.format(hex_key=key)` interpolated the key with no validation. A key containing a
`"` (e.g. `'ab"; DROP …'`) closed the PRAGMA string, so `conn.execute()` saw two statements and
raised `sqlcipher3.dbapi2.Warning` — **not** wrapped as `DatabaseKeyError` — and the connection
opened just before was **leaked** (the `except` only caught `DatabaseError`). Not reachable
today (`generate_key()` always yields 64 lowercase hex and `resolve_key` only ever returns
generated-or-stored values), but zero defense-in-depth on the one spot key text meets SQL.
**Fix:** validate `re.fullmatch(r"[0-9a-fA-F]{64}", key)` at the top of `_sqlcipher_connect` →
`DatabaseKeyError` before any connection is opened; widen the probe's cleanup to
`except BaseException: conn.close()` (re-raise non-`DatabaseError`). Regression:
`test_encryption.py::test_malformed_key_raises_database_key_error_not_a_leak` (parametrized:
empty, short, non-hex, 65-char, quote-injection). **This fix also surfaced the 65-char test
constants** — see the header note; the four constants in `test_encryption.py` / `test_health.py`
/ `test_backup_manager.py` were corrected to 64 hex.

### FINDING 2.1-F2 — LOW — `SQLiteVectorStore` construction-time `quick_check` only handled the "row returned" case — `[FIXED]`

`src/common/sqlite_vector_store.py::__init__` did `self._conn.execute("PRAGMA quick_check").fetchone()`
and checked the result. On a corrupt **encrypted** DB the pragma *raises* a driver
`DatabaseError` instead of returning a row, which would propagate raw rather than as the
store's intended `RuntimeError("… restore from a backup …")`. Pre-2.1 code, explicitly left
as-is by 2.1, harmless while the store isn't key-wired — but Phase 3 will pass a key here.
**Fix:** wrap the pragma in `except database_errors()` and fold it into the same
`RuntimeError`. Regression:
`test_health.py::test_sqlite_vector_store_reports_corrupt_encrypted_db_as_runtimeerror`.

### CONCERN 2.1-C1 — LOW — "offer the restore flow" is detection-only at 2.1 — `[DOCUMENTED]`

The acceptance criterion reads *"If the result is not `"ok"`, offer the restore-from-backup
flow."* Step 2.1 delivers the detection primitive (`check_integrity`) but nothing calls it on
launch and nothing routes a non-ok result anywhere — there is no backend `main()`, and
`BackupManager` did not exist until 2.2. Legitimately a primitives-only delivery, but the
wiring must not fall through the cracks: **Phase 3 Step 3.1 needs an explicit checklist item**
— on-launch `check_integrity` → the restore offer, and `PreviousDataUnrecoverableError` → the
"starting fresh" screen.

### CONCERN 2.1-C2 — LOW — `connect_for_migrations` changed the read-connection `isolation_level` — `[DOCUMENTED]`

`MigrationRunner`'s SELECT-only helpers (`_applied_versions_no_lock`, `status`, the pre-snapshot
read) moved from `sqlite3.connect(path)` (driver default `isolation_level=""`) to
`connect_for_migrations(path, key)` (defaults `isolation_level=None`, autocommit). Functionally
identical for read-only use — autocommit is arguably cleaner (no lingering read transaction).
Noted only because the "byte-for-byte old behaviour" claim glosses it.

### OBSERVATION 2.1-O1 — LOW — libsqlcipher writes HMAC-failure diagnostics to stderr — `[DOCUMENTED]`

`ERROR CORE sqlcipher_page_cipher: hmac check failed …` is written straight to fd 2 by the C
library on every wrong-key / corrupt open — not Python logging, not catchable. Invisible in a
packaged GUI app; noisy in dev/test. Cosmetic.

---

## Step 2.2 — Backup, Restore, and Export (`a9cfa1f`)

Independent verification built keyed and plaintext databases, took and pruned real backups,
restored over a mutated live DB, corrupted snapshots, ran the online backup against an open
write transaction, and exercised full-wipe with a raising bridge. **All 7 acceptance criteria
PASS.**

**PASS — AC1–AC7:**
- Backup produces a header-verified SQLCipher snapshot that opens with the key and is
  unopenable by stdlib `sqlite3`; plaintext source → plaintext snapshot.
- Restore reverts a mutation, passes `integrity_check` post-restore, returns
  `RestoreResult(ok=True, needs_restart=True)`, clears `-wal`/`-shm`.
- Restore of a corrupted snapshot returns `ok=False` and leaves the live DB **byte-identical**;
  no `.incoming` left (after 2.2-F1).
- Export contains exactly the 8 substantive tables; `session_chunks` absent; `deleted_at` and
  `sync_metadata` stripped from every row; soft-deleted rows excluded.
- Badge: `None` → `needs_export=True` + the exact "Never …" string; 40 d → True; 10 d → False;
  boundary 30 d → False, 31 d → True.
- Full wipe soft-deletes every row of all 10 `WIPE_TABLES` (nothing hard-deleted),
  calls `bridge.cancel_all()` **once and before** the soft-delete commit, keeps `active_model`,
  resets `last_exported_at`; a raising `cancel_all` does not abort the wipe.

**PASS** — Retention keeps exactly `retention` newest files; `list_backups()` sorts by
**filename** (ISO-µs stamp == chronological), independent of mtime. Online `source.backup(dest)`
succeeds while another connection holds an open uncommitted write transaction and the snapshot
correctly excludes that row. Every table name in an f-string is an internal literal from
`EXPORT_TABLES` / `WIPE_TABLES` — no injection surface. `write_export` is atomic and its
streamed output parses **equal** to `export_data()`. `AppConfig` v1 files (no
`last_exported_at`) load fine.

### FINDING 2.2-F1 — MEDIUM — `restore()` raised instead of returning `RestoreResult(ok=False)` when the live DB was open, and left a `.incoming` file — `[FIXED]`

`restore()` returned a structured `RestoreResult` for every *other* failure (missing / unreadable
/ failed-integrity snapshot) but the file swap itself was unguarded. On Windows, if any
connection to the live DB was still open (backend "not fully stopped"),
`os.replace(incoming, db_path)` raised **`PermissionError` uncaught** and `<db>.incoming` was
orphaned. This is the data-recovery path — a caller inspecting `result.ok` got an exception
instead of a result. **Fix:** wrap copy + sidecar-unlink + `os.replace` in `except OSError` →
`RestoreResult(False, False, "could not swap in the snapshot: …")`, `unlink(missing_ok=True)`
the `.incoming` on failure. Also moved the `-wal`/`-shm` unlink to **before** `os.replace`
(2.2-F5) so a crash in that window can't pair the new file with the old WAL. Regression:
`test_backup_manager.py::test_restore_with_the_live_db_still_open_never_raises_and_leaves_no_incoming`.

### FINDING 2.2-F2 — LOW/MEDIUM — `run_due_backup` idempotency + filename stamp only correct for a UTC `now` — `[FIXED]`

The once-per-day guard compared `now[:10]` (the caller's local date) against `snap.created_at[:10]`
(always UTC, from the filename). For a non-UTC caller whose evening crosses the UTC day
boundary (`2026-03-01T23:00:00-05:00` → UTC `03-02`), the two dates disagreed and a **duplicate
backup** was taken the same local day. Not triggered today — `SchedulerThread._tick` passes
`now_iso()` (UTC) — but a latent bug. **Fix:** normalise `now` to UTC once, up front, and use
that single UTC date for the `daily_time` check, the dedup check, and the filename stamp.
Regression:
`test_backup_manager.py::TestRunDueBackup::test_non_utc_now_does_not_double_backup_across_the_utc_day_boundary`.

### FINDING 2.2-F3 — LOW — `daily_time` fires at UTC, not "local wall-clock" — `[DOCUMENTED + comment fixed]`

`config/features/backup.yaml` said *"Local wall-clock time"* but `run_due_backup` builds
`due_at` from a UTC `now`, so the `02:00` default fires at 02:00 **UTC** regardless of the
user's timezone. The config comment and docstrings were corrected to say UTC.
**True local-time scheduling is a Phase 3 refinement** — it needs a user-timezone context the
backend does not have yet.

### FINDING 2.2-F4 — LOW — `write_export` orphaned a `*.tmp` on a mid-stream error — `[FIXED]`

No `try/finally` around the open/stream/`os.replace`. A serialization or I/O error mid-export
left `<name>.tmp` behind (self-healing on the next successful export). **Fix:**
`except BaseException: tmp.unlink(missing_ok=True); raise`. Regression:
`test_data_admin.py::test_write_export_removes_the_tmp_file_on_a_mid_stream_error`.

### FINDING 2.2-F5 — LOW — restore crash window between `os.replace` and the sidecar unlink — `[FIXED]`

Folded into 2.2-F1: the `-wal`/`-shm` unlink now happens **before** `os.replace`, so a crash in
the tiny window can no longer leave the freshly-restored file next to a stale-salt WAL.

### FINDING 2.2-F6 — LOW (doc) — Settings strings were split arbitrarily — `[FIXED]`

The "Never exported" line lived in the backend (`_NEVER_EXPORTED_LINE`), but the uninstall
warning and the export blurb existed only in the spec docs — so Phase 3 would re-transcribe two
of three spec strings by hand and risk drift. **Fix:** all three are now module constants in
`data_admin.py` (`NEVER_EXPORTED_LINE`, `UNINSTALL_WARNING`, `EXPORT_BLURB`) for the frontend to
import verbatim. Regression: `test_data_admin.py::test_settings_strings_are_exported_verbatim_from_spec`.

### CONCERN 2.2-C1 — LOW — `DataManager` / handlers opened via `db_path` never close their connection — `[DOCUMENTED]`

No `close()` / context-manager on `DataManager`. This is consistent across the whole feature
layer (`TableHandler` subclasses are the same) and is a Phase 3 lifecycle concern — the
`ShutdownCoordinator` will register connection closes.

### CONCERN 2.2-C2 — LOW (doc) — "Resets app config" is partial — `[DOCUMENTED]`

`full_wipe` keeps `active_model` and only clears `last_exported_at`. This is deliberate and
well-reasoned (wiping *data* should not force the user back through model selection) and is
documented in the code and commit — but the roadmap acceptance text still reads "Resets app
config" unqualified. Recorded here.

### CONCERN 2.2-C3 — LOW — "restart the Python backend subprocess" is a seam, not an action — `[DOCUMENTED]`

Restore returns `RestoreResult(needs_restart=True)`; nothing acts on it (no supervisor).
Same Phase 3 Step 3.1 checklist item as 2.1-C1.

---

## Step 2.3 — Crash Recovery and Process Supervision (`fde28c1`)

Step 2.3 is deliberately ~90% deferred to Phase 3. Independent verification audited **both**
the small backend slice that landed **and** the honesty of the deferral.

**PASS — the deferral is architecturally honest.** `project_logic.md` §7 explicitly assigns
single-instance *enforcement*, the backoff supervisor, the degraded-mode banner, and per-IPC
timeouts to the Tauri Rust shell / React frontend. There is no `src-tauri/`, no backend
`main()`, no IPC transport. The commit and the (now superseded) `PHASE_2_STEP_2_3.md` do **not**
claim to meet the roadmap's Step 2.3 acceptance criteria — all three are Tauri-fuzzing-based —
they state the criteria are Phase 3. No overclaim. The buildable-now candidates (a SIGTERM
handler, a readiness probe, actual single-instance enforcement) genuinely need the Phase 3
entrypoint/IPC/window; the reusable mechanisms did land.

**PASS — `ShutdownCoordinator`:** teardown runs in reverse registration order; a raising step
is isolated (logged, `ok=False` with the exception text) and the rest still run; a second
`shutdown()` is a true no-op returning `[]`; `register()` after shutdown raises; `elapsed_ms`
is really measured. `_done` is set before the loop, so a re-entrant signal mid-shutdown is
safe.

**PASS — `SingleInstanceGuard`, cross-process:** a spawned holder process took
`Local\audit_test`; the foreground `acquire()` on the same name returned **False**, a different
name returned **True**, and after the holder exited the name was reacquirable. In-process:
first True / second False, `release()` frees it, `__enter__` raises `AlreadyRunningError`,
`acquire`/`release` idempotent. Import-safe on non-Windows (`ctypes.windll` only touched inside
the `win32` branch).

**PASS — the HTTP-timeout AST test can actually fail:** adding an unbounded `requests.get(...)`
under `src/` made the test fail and name the `file:line`; reverted. A grep of all of `src/`
found only `requests` (in `llm_client.py`, `model_manager.py`, `ollama_manager.py`) and every
call carries an explicit `timeout=`. No `httpx` / `urllib` / raw socket.

**PASS — `shutdown_tracing()`:** no-op when disabled; calls `client.shutdown()` once with a
fake client; swallows a raiser.

**PASS — write-path crash-safety spot-checks:** all five feature handlers use `conn.commit()`
for single writes and `with self._conn:` for multi-statement units — no handler leaves a
transaction open or does an unbraced multi-statement write. `app_config.json` is tmp +
`os.replace` (atomic on Windows). `metrics_store` opens/closes a fresh WAL connection per call.

### FINDING 2.3-F1 — MEDIUM — pre-migration `.bak` had the identical partial-file risk that 2.3 fixed for daily backups — `[FIXED]`

`db/migration_runner.py::snapshot()` did `source.backup(dest)` straight to the final
`<stem>-pre-migration-<ts>.bak` — the exact pattern §3 of the old step doc identified as "the
one gap found" and fixed for `BackupManager` with `.partial` + `os.replace`. The doc disclosed
it ("Low priority; not changed") but then said "No other gaps found," which was inaccurate — the
same gap existed and was consciously left. **Fix:** apply the same `.bak.partial` +
`os.replace` discipline to `snapshot()`. Regression:
`test_migration_runner.py::TestRun::test_snapshot_leaves_no_partial_file`.

### FINDING 2.3-F2 — LOW — `shutdown_tracing()` was not idempotent and left a dead client in module globals — `[FIXED]`

After `shutdown_tracing()`, `observability.tracing._client` still pointed at the shut-down
client, so a later `get_langfuse()` / `flush()` reused it and a **second `shutdown_tracing()`
called `client.shutdown()` again**. Benign in the documented teardown order (tracing torn down
last) but `ShutdownCoordinator` is idempotent and this wasn't. **Fix:** reset `_client = None`
(and keep `_client_init_attempted = True`) in a `finally`, so a repeat call and any later
`flush()` are genuine no-ops. Regression:
`test_shutdown_tracing.py::test_second_shutdown_tracing_is_a_no_op`.

### FINDING 2.3-F3 — LOW — `ShutdownCoordinator` had no lock — `[FIXED]`

`register()` / `shutdown()` mutated `self._steps` / `self._done` with no synchronisation,
despite the class existing precisely to be called from a signal context (a stop signal on one
thread while `main()` is still wiring on another). **Fix:** a `threading.Lock` guards both;
`shutdown()` snapshots the step list under the lock and runs the callables outside it.
Regression: `test_lifecycle.py::test_concurrent_register_and_shutdown_do_not_corrupt_the_step_list`
(50 concurrent registrars racing `shutdown()`).

### FINDING 2.3-F4 — LOW — `SchedulerThread.stop()` silently reported success on a join timeout — `[FIXED]`

`stop()` did `self.join(timeout)` and returned `None` unconditionally — a wedged tick left the
thread running while `ShutdownStepResult.ok` was `True`, a false positive. **Fix:** `stop()`
now returns `bool` (`False` + a `logger.warning` if `is_alive()` after the join). Regression
in `test_lifecycle.py::test_a_real_scheduler_thread_is_joined`.

### FINDING 2.3-F5 — LOW — the HTTP-timeout AST test only matched `requests.<method>(...)` — `[FIXED]`

`s = requests.Session(); s.get(url)` (a common pattern), `httpx`, and `**kwargs` splat calls
were all invisible to the check — narrower than the roadmap intent ("every outbound HTTP call
bounded"). None exist in `src/` today, but the "CI lock" had a hole. **Fix:** the AST walk now
tracks local names bound to an HTTP client module or a `Session()` / `Client()` built from one,
and scans `httpx` / `urllib3` too. Renamed `test_no_unbounded_http_call_in_src`.

### CONCERN 2.3-C1 — MEDIUM — "One restart path, not two" oversimplified restore vs. crash — `[DOCUMENTED — reworded below]`

The old step doc claimed `RestoreResult.needs_restart` and a crash "both resolve to the same
Phase 3 action." A **crash** needs only `{relaunch}` — the process is dead, nothing holds the
DB. A **restore** needs `{quiesce connections → swap the file → relaunch}`, and the ordering is
load-bearing: `BackupManager.restore()` currently performs the `os.replace` from *inside the
running backend* (2.2-F1 makes that fail gracefully now, but it still can't succeed while the
`SchedulerThread` holds a connection). **Whether the file swap belongs in Python
(`BackupManager.restore`, backend still up) or in Rust (backend fully down) is an open Phase 3
Step 3.1 decision, not something already settled.** The corrected statement is in the
"Phase 3 hand-off" section below.

### CONCERN 2.3-C2 — LOW — no `fsync` on the atomic-write paths — `[DOCUMENTED]`

`app_config.save`, the two `.partial`→final backups, and `restore()`'s `.incoming`→live all do
write/copy then `os.replace` with no `fsync` of the file or directory. `os.replace` is fully
atomic for a **process** crash (this audit's stated scope). A host **power-loss** between the
content write and the rename metadata reaching disk could yield a zero-length target. For a
local single-user companion app, power-loss durability is reasonably out of scope — recorded so
the decision is explicit, not accidental.

---

## Phase 3 hand-off (carried forward)

1. **Startup wiring** (2.1-C1, 2.2-C3): Phase 3 Step 3.1's backend `main()` must, on launch:
   resolve the key (`PreviousDataUnrecoverableError` → the "starting fresh" screen), run
   migrations, `check_integrity` → **if not ok, offer the restore flow before any other data
   access**, then wire `RestoreResult.needs_restart` to the supervisor.

2. **One restart *sequence*, two triggers** (2.3-C1): a backend **crash** → the supervisor
   relaunches (exponential backoff 1s/2s/4s, 3 attempts, then degraded-mode banner). A
   **restore** → the supervisor must first stop the backend (quiescing every DB connection),
   *then* perform the file swap, *then* relaunch. Decide where the swap runs (Python
   `BackupManager.restore` with the backend still up, vs. a Rust-side swap with it down) as part
   of Step 3.1 — they are not interchangeable.

3. **Tauri-owned** (unchanged from the scope split): the process supervisor + backoff, the
   degraded-mode banner, per-IPC-call timeouts → "temporarily unavailable" state, the
   single-instance mutex **enforcement** + bring-first-window-to-foreground, and the
   subprocess-kill fuzzing test on `windows-latest`.

4. **Real WinRT `ToastBridge`** (Phase 3 Step 3.1): `cancel_all()` must iterate registrations
   and `RemoveFromSchedule` per id — Windows has no bulk-cancel API. `InMemoryToastBridge`
   models this correctly (records + clears all ids).

5. **Deferred non-functional gates:** Step 2.4 accessibility (WCAG 2.1 AA / axe-core / Narrator
   / string externalization) is entirely Phase 3. Step 2.5 performance benchmarking runs
   pre-Phase-5 on §9.1 reference hardware (8th-gen i5 / Ryzen 5, 16 GB, SATA SSD, iGPU only):
   the 10,000-session-chunk vector-search benchmark, idle CPU/RAM with the full stack, and
   cold-start are the authoritative pre-submission gates, committed as a per-CI-run JSON
   artifact.

6. **Local-time backup scheduling** (2.2-F3): `daily_time` is UTC until the backend has a
   user-timezone context.

7. **`SQLiteVectorStore` key wiring** (2.1-F2 is fixed defensively; the store still isn't
   key-wired) — Phase 3 passes the key through here.

---

## Remediation summary

**Fixed with regression tests in the remediation commit:**

| # | Area | Fix |
|---|---|---|
| 2.1-F1 | `db/connection.py` | 64-hex key validation → `DatabaseKeyError`; probe cleanup widened to `BaseException` (no leaked connection). Corrected 4 × 65-char test key constants. |
| 2.1-F2 | `src/common/sqlite_vector_store.py` | construction `quick_check` catches driver `DatabaseError` → the store's `RuntimeError`. |
| 2.2-F1 / F5 | `src/features/backup_manager.py::restore` | guarded file swap → `RestoreResult(ok=False)` on `OSError`; `.incoming` cleanup; sidecar unlink moved before `os.replace`. |
| 2.2-F2 | `src/features/backup_manager.py::run_due_backup` | `now` normalised to UTC for the guard + stamp. `backup.yaml` comment corrected. |
| 2.2-F4 | `src/features/data_admin.py::write_export` | `try/except BaseException` removes the orphan `*.tmp`. |
| 2.2-F6 | `src/features/data_admin.py` | `NEVER_EXPORTED_LINE` / `UNINSTALL_WARNING` / `EXPORT_BLURB` co-located as constants. |
| 2.3-F1 | `db/migration_runner.py::snapshot` | `.bak.partial` + `os.replace`. |
| 2.3-F2 | `observability/tracing.py::shutdown_tracing` | reset cached client → idempotent. |
| 2.3-F3 | `src/backend/lifecycle.py` | `threading.Lock` around `register` / `shutdown`. |
| 2.3-F4 | `src/features/scheduler.py::stop` | returns `bool` + warns on join timeout. |
| 2.3-F5 | `tests/backend/test_http_timeouts.py` | AST walk broadened to `Session()` / `Client()` / `httpx` / `urllib3`. |

**Documented — not fixed (judgment calls / Phase 3):** 2.1-C1, 2.1-C2, 2.1-O1, 2.2-C1, 2.2-C2,
2.2-C3, 2.2-F3 (local-time), 2.3-C1 (reworded), 2.3-C2 (power-loss out of scope), and the
Phase 2 Milestone status (Cross-Cutting).

**No finding was rejected.** No HIGH-severity issue was found.
