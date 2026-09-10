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
| 4.1b | The five required end-to-end flows | **DONE** |
| 4.2  | IPC contract verification (every method, valid + invalid, version N vs N+1) | pending |
| 4.3  | Golden eval pass — routing remediation + CI eval dispatch | pending |
| 4.4a | disk-full + network-loss download simulation (roadmap acceptance wording) | pending |
| 4.4b | subprocess-kill fuzzing harness + `reliability` CI job | pending |
| 4.5  | WCAG 2.1 AA / Narrator accessibility pass (former roadmap Step 2.4) | pending |
| 4.6  | Real WinRT ToastBridge | pending |

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
