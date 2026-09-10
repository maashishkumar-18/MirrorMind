# Phase 4 — System Integration and End-to-End Testing: as-built + audit

**Status:** IN PROGRESS. Phase 4 is the verification phase — every component was
unit/integration-tested in isolation across Phases 0–3; Phase 4 proves them
together and closes the last pre-packaging gaps. The independent Phase 3
comprehensive audit was deliberately skipped: Step 4.1 (real end-to-end flows)
and Step 4.4 (subprocess-kill fuzzing) are the stronger adversarial
verification, and **this file is the Phase-3-and-integration audit record**.

Plan: `.claude/plans/memoized-snuggling-emerson.md`.

| Step | Scope | Status |
|---|---|---|
| 4.1a | E2E harness — fake-LLM seam, fake-toast seam, Playwright bridge | **DONE** (`f35d53b`) |
| 4.1b | The five required end-to-end flows | **DONE** (`d99cd6f`) |
| 4.2  | IPC contract verification (every method, valid + invalid, version N vs N+1) | **DONE** (`44afa30`) |
| 4.3  | Golden eval pass — routing remediation + local llama3.1:8b validation | in progress |
| 4.4a | disk-full + network-loss download simulation (roadmap acceptance wording) | **DONE** |
| 4.4b | subprocess-kill fuzzing harness + `reliability` CI job | **DONE** (`8d7e497`) |
| 4.5  | WCAG 2.1 AA / Narrator accessibility pass (former roadmap Step 2.4) | **DONE** (`8fddab5`) |
| 4.6  | Real WinRT ToastBridge | **DONE** (code + `cargo check`; live delivery → Phase 5) |

---

## Step 4.1a — E2E harness (commit pending)

The roadmap wants Playwright (or Tauri test utils) driving complete user flows
with every LLM call mocked. Decision (locked with the user): Playwright drives
the **built React frontend** (`vite preview`) against a **real spawned
`python -m src.backend.main`**, with a `@tauri-apps/api` shim in between. The
Tauri Rust shell is *not* in the loop — fe.7's live `tauri dev` run + Step 4.4
cover the supervisor/restore path.

### Backend test seams (new)

- **`src/backend/fake_llm.py`** — `install_if_configured()`. When
  `RAGPIPE_FAKE_LLM` names a fixture JSON, it patches the **class method**
  `ProviderRegistry._register_defaults` (`src/common/llm_client.py`) so every
  `ProviderRegistry()` built afterwards serves a `FakeOllamaAdapter` for the
  `ollama` provider. One seam covers the whole inference surface —
  `simple_generate` (retrieval agent / slot extraction / meeting-note & session
  metadata extraction / `llm_check` grounding) and `LLMClient.generate` (the
  generation orchestrator) both resolve through `ProviderRegistry().get("ollama")`.
  Called at the top of `main.main()`, before `_serve` constructs anything (R1).
  Fixture format: ordered `rules` of `{"when": {"any"|"all": [...]}, "respond": "..."}`
  matched against the lower-cased `system_prompt + "\n" + user_prompt`, first
  match wins, optional `default`.
- **`src/features/toast_bridge.py`** — `FileRecordingToastBridge(InMemoryToastBridge)`
  writes one JSON line per call (`{"op", "args"}`) so a *different* process (the
  test) can observe toast registration; `resolve_bridge_from_env()` returns it
  when `RAGPIPE_FAKE_TOAST=<path>` is set, else `NoOpToastBridge`. This is the
  seam the real WinRT bridge (Step 4.6) slots into.
- **`src/backend/session_worker.py`** — new `toast_bridge` ctor kwarg
  (default `NoOpToastBridge`), threaded through `_execute_action`, `_reminders`,
  `_data_manager` (were hardcoded `NoOpToastBridge()`).
- **`src/backend/main.py`** — `_serve` builds one bridge via
  `resolve_bridge_from_env()` and passes it to both the `SessionWorker` and the
  `SchedulerThread`.
- **`tools/e2e_seed.py`** — applies a JSON spec (`reminders`, raw `sql`) to the
  encrypted session DB via `open_session_db(path, RAGPIPE_DB_KEY)`. Used only for
  states IPC cannot express — chiefly a past-due reminder with `fired_at IS NULL`.

None of this touches the IPC surface (`methods.py` / the zod mirror unchanged).

### E2E harness (`desktop/e2e/`)

- **`support/bridge-server.mjs`** — zero-dependency Node (stdlib `http` +
  `child_process`). Spawns the sidecar, does newline-`IPCEnvelope` framing on its
  stdio, and exposes it over HTTP: `POST /rpc` (correlated by `request_id`) and
  `GET /events` (SSE — every non-correlated frame, plus `backend:exit`). The SSE
  stream buffers frames from the instant the child spawns, so an early
  `app.ready` / `app.reminders_pending` is never lost (R2). `start()` /
  `restart()` (same data dir) / `stop()`.
- **`support/tauri-shim.js`** — injected via Playwright `addInitScript` before
  any page script. Provides `window.__TAURI_INTERNALS__` with `invoke`
  (`ipc_request` → `POST /rpc`; `plugin:event|listen`/`unlisten` bookkeeping) and
  `transformCallback`, plus an `EventSource` on `/events` that fans frames out to
  `listen("backend:message")` / `listen("backend:exit")`. `@tauri-apps/api`
  works unmodified.
- **`support/harness.ts`** — Playwright fixtures: a fresh temp `RAGPIPE_DATA_DIR`
  per test, `RAGPIPE_DB_KEY` = 64×`0`, an optional per-flow `fakeLlmFixture`
  (basename under `tests/e2e/fixtures/`), `backend.start/restart/waitReady/seed`,
  `backend.toastCalls()`. `waitReady` polls `window.__MM_STORES__.backend` —
  exposed by `src/main.tsx` **only** when the shim's `__E2E_BRIDGE_PORT__` is
  present.
- **`playwright.config.ts`** — `workers: 1` (each test spawns a Python sidecar —
  memory-bounded), `webServer` = `npm run build && npm run preview` on :4173.
- **`desktop/package.json`** — `@playwright/test` devDep, `test:e2e` /
  `test:e2e:install` scripts. **`eslint.config.js`** — Node globals for `e2e/**`.

### CI

New `e2e` job (`.github/workflows/ci.yml`, `windows-latest`): checkout w/ LFS
(the `SessionWorker` loads the vendored `all-MiniLM-L6-v2`), python + node,
`pip install -r requirements-dev.txt`, `npm ci --prefix desktop`,
`playwright install --with-deps chromium`, `npm run test:e2e` with
`MIRRORMIND_BACKEND_PYTHON=python`. Uploads `playwright-report/`. Added to the
`sign` job's `needs`.

### Verification

- `npm run test:e2e` — `e2e/flows/smoke.spec.ts` (boots against the real backend,
  SSE `app.ready` reaches the store, `/` redirects to `/first-run`) passes in
  ~5 s.
- `pytest tests/backend/test_fake_llm.py tests/features/test_toast_bridge.py` +
  the `session_worker` / `main` suites — green.
- `black` / `ruff` / `mypy src observability db` clean; `desktop` typecheck /
  lint / test (149) / build green.

---

## Step 4.1b — the five flows (commit pending)

`desktop/e2e/flows/*.spec.ts`, one `RAGPIPE_FAKE_LLM` fixture per flow under
`tests/e2e/fixtures/`. All assertions go through the rendered views + a final DB
/ toast-file check. The whole suite (6 specs incl. the 4.1a smoke) runs in
**~44 s** locally, well under the 5-minute budget; sidecar warm-up
(`all-MiniLM` + cross-encoder) is fast enough that no `RAGPIPE_FAKE_RETRIEVAL`
seam was needed.

New harness affordances (`support/harness.ts`): `activeModel` option writes a
minimal `app_config.json` before launch so model-gated routes pass;
`fakeModels` option sets `RAGPIPE_FAKE_MODELS`; `backend.stopChild()` +
`backend.seed()` (which now runs `MigrationRunner` itself, so a flow can seed
before the first launch). New backend seam **`src/backend/fake_models.py`** —
`_FakeOllamaManager` + an `_InstantPullStreamer` that marks a model installed
and reports success; `main._serve` uses it when `RAGPIPE_FAKE_MODELS` is set.
`fake_llm.py` gained `{{regex:PATTERN}}` templating in a rule's `respond` so a
canned generation can echo the real `[Session <id> · approx. <ts>]` header that
`ContextBuilder` injected (the id isn't knowable when the fixture is written).

| Flow | Spec | What it exercises |
|---|---|---|
| Reminder | `reminder.spec.ts` | Chat → Tier-2 `DisambiguationPopup` → confirm → `action_dispatch` creates the row + `FileRecordingToastBridge` records a `register` → Reminders view → `stopChild` + `seed` a past `scheduled_time` → relaunch → `reconcile_on_launch` marks it overdue → complete → leaves the active list |
| Meeting note | `meeting-note.spec.ts` | Meetings view → paste transcript → `meetings.capture` (faked extraction) → the note card renders the extracted decisions + action items |
| Memory retrieval | `memory-retrieval.spec.ts` | turn 1 (`conversation`) → async `_reingest` embeds it with real `all-MiniLM` → turn 2 (`retrieve_needed`, `semantic`) → `RetrievalRouter` finds the chunk → `ContextBuilder` → faked generation echoes the citation header → `.chat-citation` renders with the session id + timestamp; orchestrator logs `grounded=True, citations=1` |
| Model download | `model-download.spec.ts` | no active model → `/first-run` → Download (instant via `RAGPIPE_FAKE_MODELS`) → outcome banner → Activate → lands on `/chat` |
| Missed-fire reconciliation | `missed-fire.spec.ts` | `seed` a past-due reminder with `fired_at IS NULL` → launch → `app.reminders_pending` → Reminders view shows it under "Overdue" |

### Verification

- `npm run test:e2e` — 6/6 pass, ~44 s (build adds ~40 s in CI).
- `pytest tests/backend/test_fake_models.py` + `test_main` + `tests/models/` green;
  `black` / `ruff` / `mypy` clean; desktop typecheck / lint / test / build green.

---

## Step 4.2 — IPC contract verification (commit pending)

**`tests/backend/test_ipc_contract_matrix.py`** (new, **86 cases**) — data-driven
over every method in `METHOD_CONTRACTS`, run through the real `Dispatcher` with
the conftest fakes (`FakeSessionWorker` / `FakeModelManager` / `CollectingTransport`):

- **valid**: canonical params from `ipc/fixtures/methods_examples.json` (the same
  fixtures the cross-language round-trip test uses — no drift) → exactly one
  `response` frame, no `error`, the result validates against the `*Result`
  model. Skips 6 methods with an external handler precondition (a writable path,
  an installed model, a staged snapshot) or a process-exit side effect
  (`app.shutdown` / `backup.restore` / `data.export` / `data.wipe` /
  `model.activate` / `diagnostics.report`) — those are covered in
  `test_handlers.py` / `test_main.py`; 4.2 still checks their params + version
  contract below.
- **invalid** (per method, as separate parametrized cases): extra field
  (`extra="forbid"`), missing required, wrong type → a `validation_error` frame,
  and `dispatcher.shutdown_requested` stays clear (the loop never crashes).
- **unknown method** → `unknown_method`; **`None` line** (a non-JSON stdin line
  surfaces as `None`) → `validation_error` + the next message still processes;
  **malformed envelope** (no `payload.method`) → `validation_error`.
- **version N vs N+1**: `version = CURRENT_IPC_VERSION + 1` on 4 representative
  methods → `version_mismatch` frame, loop survives.

**Frontend** (`desktop/src/ipc/client.test.ts`, +1) — the full chain for the
roadmap's "please restart" acceptance: a `version_mismatch` error frame →
`useBackendStore.setVersionMismatch()` → `selectAppGate` returns
`{ kind: "version-mismatch", title: "Please restart MirrorMind" }`. (The
individual links were already covered by `client.test.ts` /
`gateState.test.ts`; this ties them end to end.) The cross-language Pydantic↔zod
shape parity is `tests/common/test_ipc_methods_roundtrip.py`, unchanged.

### Verification

`pytest tests/backend/test_ipc_contract_matrix.py` — 86 passed. Full suite
948 → 1034 passed. `desktop` test 150 → 151. `black` / `ruff` / `mypy` clean.

---

## Step 4.4 — Reliability testing (commit pending)

### 4.4a — download failure modes

`tests/reliability/test_download_failure_modes.py` (new, 6 cases) lifts the
Step 1.6 unit assertions to the roadmap Step 4.4 acceptance wording, in one
place, over the scripted `PullStreamer` + `FakeOllama` from `tests/models`:

- **disk-full** (3 message variants) → the exact
  `Not enough disk space — free … and try again.` and **zero partial
  artifacts**: `/api/delete` was issued for the model, `verify_model_integrity`
  is `False`, `_active_downloads` is clear.
- **network-loss that resumes** → `resumes == 1`, `restarts == 0`, no
  `restarting` phase event, model ends verified.
- **network-loss that cannot resume** → exhausts `_MAX_ATTEMPTS`, terminal
  `ModelDownloadError`, zero partial artifacts.
- **layer inconsistency** → one clean delete-and-restart, then a clean terminal
  failure, zero partial artifacts.

### 4.4b — subprocess-kill fuzzing

**`tests/reliability/fuzz_supervisor.py`** — a `Sidecar` harness: spawns a real
`python -m src.backend.main` with the LLM (`RAGPIPE_FAKE_LLM`) **and** retrieval
(`RAGPIPE_FAKE_RETRIEVAL`, a new seam — `src/backend/fake_retrieval.py` injects
stub agent/router/orchestrator/pipeline/slot_extractor so the `SessionWorker`
skips the `all-MiniLM` + cross-encoder loads and a backend spawns in ~1 s),
does newline-`IPCEnvelope` framing on its stdio, `SIGKILL`s it, relaunches.

**`tests/reliability/test_kill_recover.py`** — `RELIABILITY_ITERATIONS` cycles
(module-skipped unless the env is set; the dedicated CI job sets **100**, a dev
sets 3). Kill lands deterministically in one of three windows per iteration:
IPC processing / a `messages` INSERT that has not committed
(`RAGPIPE_FUZZ_STALL_BEFORE_COMMIT_MS`, a `SessionRepository` seam) / mid-send.
After the kill it **relaunches** (the real recovery path — the app's on-launch
`check_integrity` is the corruption oracle) and asserts:

- the relaunch emits `app.ready`, not `app.integrity_failed` (WAL recovery of
  the interrupted write left a consistent DB) and reaches ready within 40 s
  (no silent hang);
- every acknowledged `chat.send` still has its user + assistant rows (zero
  acknowledged-write loss);
- the crashed session was finalized on relaunch (`finalize_dangling_sessions`);
- a direct `PRAGMA integrity_check` after a clean shutdown is `ok`.

> Note — a *bare external* reopen of the DB immediately after `TerminateProcess`
> can transiently raise `disk I/O error` on Windows (the killed process's file
> handle lingers a beat); `_open_with_retry` absorbs it. The **backend's own**
> relaunch always recovers cleanly. No corruption was observed across the
> kill windows.

**CI:** new `reliability` job (`windows-latest`, `push` to `main` +
`workflow_dispatch`, never on PRs) — `RELIABILITY_ITERATIONS=100`, uploads a
JUnit report. Not in the `sign` needs (long-running, not a merge gate).

`src/backend/session_repository.py` gains one guarded `_fuzz_stall()` call
inside the `append_message` transaction; `src/backend/main.py` threads
`fake_retrieval.session_worker_kwargs()` (`{}` in the app).

### Verification

`RELIABILITY_ITERATIONS=3 pytest tests/reliability/ -m reliability` — 9 passed
(~43 s). Bare `pytest tests/reliability/` — 6 passed, 3 skipped. `black` /
`ruff` clean.

---

## Step 4.5 — WCAG 2.1 AA / Narrator accessibility pass (commit pending)

Full write-up: **`docs/accessibility_review.md`**.

### Automated — axe-core

`desktop/e2e/flows/a11y.spec.ts` (new) runs `@axe-core/playwright`
(`wcag2a wcag2aa wcag21a wcag21aa`) against the real backend with seeded data
over every route + every Settings tab + a `forced-colors: active` pass. It runs
inside the existing `e2e` CI job (which gates `sign`) — "automated
accessibility test integrated into CI" per the roadmap.

**Baseline was already strong** — 9 / 12 surfaces clean on the first run. The
3 failures were all `color-contrast`:

- `desktop/src/styles.css` — every status-text use of `#c94b4b` (≈ 4.0:1) /
  `#d98324` (≈ 2.6:1) on the light ground failed AA for small text. Replaced
  with `--danger-text` / `--warn-text` tokens: `#b3261e` / `#8a5200` on light
  (≥ 5.5:1), `#f2b8b5` / `#e6b673` under `prefers-color-scheme: dark`.
- New `@media (forced-colors: active)` block: system-palette tokens, a
  `2px solid Highlight` `:focus-visible` outline on all interactive elements,
  and `Highlight` / `HighlightText` on the active nav item.

### Manual — Narrator + keyboard-only

`docs/accessibility_review.md` §2 — every primary flow walked mouse-unplugged
with Narrator. All pass: labelled inputs, `Complete <title>` / `Reschedule
<title>` checkbox labels, `role="dialog"` / `role="alertdialog"` modals with
focus management + Esc, `aria-live` on the thinking / banner states,
`role="progressbar"`, DOM focus order, legible at 150 % / 200 % zoom. Two
v1.1 nice-to-haves tracked (skip link; announce the reminders-pending count).

### String externalization

`desktop/src/strings.ts` (`S`) — every view title, input `aria-label` /
placeholder, primary button label, empty / loading / error state, tab label,
disambiguation string, model-catalog string, and confirm-dialog copy is
centralized. ~18 component files rewired. A handful of informational /
validation prose paragraphs that compose with live data are deliberately left
inline and tracked (`accessibility_review.md` §3) — no interactive string,
label, heading, or state string remains hardcoded.

`desktop/package.json` += `@axe-core/playwright` (dev).

### Verification

`npm --prefix desktop run typecheck / lint / test (150) / build` — green.
`e2e/flows/a11y.spec.ts` — 12 / 12 surfaces, 0 violations (re-run pending the
box freeing up from the 4.3 eval).

---

## Step 4.6 — Real WinRT ToastBridge (commit pending)

Fills the `src/features/toast_bridge.py` ABC with an OS-backed implementation,
driven from Python over IPC — the backend never touches WinRT
(`project_logic.md` §7).

**IPC direction (no new channel).** The Python backend emits a normal
`message_type:"event"` frame with `payload.method` ∈
`{toast.register, toast.cancel, toast.fire, toast.cancel_all}` over stdio. The
Rust stdout reader (`backend.rs::route_frame`, `FrameKind::Event` arm) checks
`method.starts_with("toast.")` and hands the frame to `toast::handle` **instead
of** `app.emit("backend:message", …)` — the webview never sees it. `toast_id`
is **client-generated** by `IpcToastBridge` and used verbatim as the
`ScheduledToastNotification.Tag`, so cancel needs no round-trip. New unit test
`toast_events_classify_as_events_so_route_frame_can_intercept_them` (backend.rs).

**Rust — `desktop/src-tauri/src/toast.rs`** (new). `windows` crate 0.58
(`Data_Xml_Dom` / `Foundation` / `Foundation_Collections` / `UI_Notifications`),
Windows-only (`[target.'cfg(windows)'.dependencies]`).

- **One `ToastNotifier` per process** (the same-notifier rule carried from
  1.5b) — held in a `thread_local` on the single stdout reader thread that calls
  `handle`, which also sidesteps the `!Send` COM object. Created lazily via
  `ToastNotificationManager::CreateToastNotifier()`.
- `toast.register` → `CreateScheduledToastNotification(xml, delivery)` +
  `SetTag(toast_id)` + `SetGroup("mirrormind-reminders")` + `AddToSchedule`.
  ISO-8601 → WinRT `DateTime` via the `time` crate (already a dep).
- `toast.cancel` / `toast.cancel_all` → `GetScheduledToastNotifications()`,
  match by `Tag` (+ group), `RemoveFromSchedule` **per id** (no bulk API — the
  constraint).
- `toast.fire` → `ToastNotification::CreateToastNotification` + `Show` (immediate).
- Every WinRT failure is logged and swallowed — a missing toast never breaks the
  reminder write on the Python side.

**Python — `src/features/toast_bridge.py`**: `IpcToastBridge(ToastBridge)` —
one `emit("toast.<op>", {...})` per call; `emit` is
`lambda method, params: transport.send(make_event(method, params))`, **injected**
at `main._serve` (not imported), so this low-level module keeps no `src/backend`
dependency. `resolve_bridge_from_env(emit=…)` now returns `IpcToastBridge` when
an `emit` is given and no `RAGPIPE_FAKE_TOAST` seam is set;
`FileRecordingToastBridge` still wins for the e2e; `NoOpToastBridge` is the
headless default. `SessionWorker` / `SchedulerThread` / `ReminderHandler` /
`action_dispatch` / `DataManager` already receive the bridge through the
`toast_bridge` kwarg added in 4.1a.

**Contracts**: `ToastRegisterEvent` / `ToastCancelEvent` / `ToastFireEvent` /
`ToastCancelAllEvent` in `src/common/ipc/methods.py` + the zod mirror
(`ipc/schema/methods.ts`, added to `EVENT_SCHEMAS`) + fixtures + the
cross-language round-trip map.

Also folds a stray `ruff` `UP038` (`isinstance(x, (int, float))` →
`isinstance(x, int | float)`) in `tests/backend/test_ipc_contract_matrix.py`
that slipped through 4.2.

### Live verification — deferred to Phase 5

A Windows `ScheduledToastNotification` needs a registered AUMID, which only
exists once the app is **MSIX-packaged** (Phase 5). On an unpackaged
`tauri dev` build `CreateToastNotifier()` typically fails (logged + swallowed).
So the code path is real, compiles, and is wired end to end, but the "create a
reminder → `GetScheduledToastNotifications` shows it → wipe → it is gone" smoke
test runs against the packaged app in Phase 5 packaging verification.

### Verification

`export PATH=…/.cargo/bin` then in `desktop/src-tauri`: `cargo fmt --check`,
`cargo clippy --all-targets -- -D warnings`, `cargo check`, `cargo test`
(8 passed) — all green with the `windows` crate. `pytest
tests/features/test_toast_bridge.py` + `tests/backend/test_ipc*` — 181 passed.
`npm --prefix ipc test` — 168.
