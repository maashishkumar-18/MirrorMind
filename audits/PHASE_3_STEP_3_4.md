# Phase 3 Step 3.4 — Settings & Diagnostics

**Status:** Step 3.4 is **COMPLETE** — 5 commits + 1 follow-up fix
(`84414b5` 3.4a, `a823891` 3.4b, `12a0a1c` 3.4c, `5bbc649` 3.4d, `0a6ab4a` 3.4e,
`<fix>` AppConfig concurrency hardening). **This closes Phase 3's feature
surface.** Next: the Phase 3 comprehensive audit (independent fresh-context
agents, per `wants-independent-verification-not-self-review`), then Phase 4.

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

## 3.4a — rotating backend log file + redaction (`84414b5`)

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

## 3.4b — settings persistence + settings / data / diagnostics IPC (`a823891`)

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

## AppConfig concurrency hardening (follow-up fix — found via the 3.4b e2e)

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

## 3.4c — Settings shell + nav + export badge + General & Models (`12a0a1c`)

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

## 3.4d — Data & Privacy + Backup & Recovery (`5bbc649`)

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

## 3.4e — Diagnostics + Report a problem (`0a6ab4a`)

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

## Verification

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
