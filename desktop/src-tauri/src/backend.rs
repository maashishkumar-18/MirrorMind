//! Python backend sidecar + the stdio<->invoke bridge + the process supervisor
//! (Phase 3 Step 3.1, fe.3 / fe.7).
//!
//! - Spawns `python -m src.backend.main` (the newline-delimited JSON `IPCEnvelope`
//!   sidecar) and reads its stdout line by line.
//! - `response` / `error` frames whose `request_id` matches a pending
//!   `ipc_request` complete that call's oneshot; every other frame is forwarded
//!   to the webview (`backend:message`, unattributed errors on `backend:error`).
//! - **Supervisor (fe.7):** on an unexpected exit the backend is respawned with
//!   exponential backoff (1s/2s/4s, 3 retries after the initial launch); after
//!   that the shell emits a give-up and the "Restart" button takes over. A clean
//!   `app.shutdown` (window close) and the deliberate exit codes 3 / 5 are not
//!   treated as crashes; exit 5 runs the restore file swap then relaunches.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::oneshot;

/// Mirror of `src/common/ipc/envelope.py::CURRENT_IPC_VERSION` — keep in sync.
const IPC_VERSION: u64 = 1;
/// How long window-close waits for the backend's `ShutdownCoordinator` before a
/// hard kill.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(10);
/// Hard ceiling on any single `ipc_request`, regardless of the caller's timeout.
const REQUEST_CEILING: Duration = Duration::from_secs(900);
/// Crash restarts after the initial launch before the supervisor gives up.
const MAX_RESTARTS: u32 = 3;
/// Backoff before each crash respawn (`BACKOFF_MS[restart_count]`).
const BACKOFF_MS: [u64; 3] = [1000, 2000, 4000];
/// Upper bound on the post-EOF reap before we hard-kill and treat it as a crash.
const REAP_BOUND: Duration = Duration::from_secs(2);

// --------------------------------------------------------------------------
// Managed state
// --------------------------------------------------------------------------

#[derive(Default)]
struct ExitHint {
    reason: Option<String>,
    snapshot_path: Option<String>,
}

impl ExitHint {
    /// Update from a lifecycle `event` frame. Pure — no `AppHandle`.
    fn observe(&mut self, method: &str, frame: &Value) {
        match method {
            "app.previous_data_unrecoverable" => {
                self.reason = Some("previous_data_unrecoverable".into());
            }
            "app.restore_staged" => {
                self.reason = Some("restore_staged".into());
                self.snapshot_path = frame
                    .pointer("/payload/params/validated_snapshot_path")
                    .and_then(Value::as_str)
                    .map(String::from);
            }
            _ => {}
        }
    }
}

#[derive(Default)]
struct Supervisor {
    /// Crash-respawns since the last successful `app.ready`.
    restart_count: u32,
    /// Set before `app.shutdown` (window close) — suppresses the restart path.
    clean_shutdown: bool,
    /// The supervisor exhausted its automatic restarts; `restart_backend` is now
    /// the only way back.
    gave_up: bool,
}

/// What the supervisor should do for a given backend exit. Pure — unit-tested.
#[derive(Debug, PartialEq)]
enum ExitAction {
    /// `reason "restore_staged"` / exit 5 — file swap then relaunch (no backoff).
    Restore,
    /// `reason "previous_data_unrecoverable"` / exit 3 — the fe.5 gate covers it.
    PreviousDataUnrecoverable,
    /// A window-close `app.shutdown` — the app is closing, do nothing.
    CleanExit,
    /// A crash, `restart_count < MAX_RESTARTS` — sleep this many ms, respawn.
    Retry(u64),
    /// A crash, retries exhausted.
    GaveUp,
}

fn decide_on_exit(
    reason: Option<&str>,
    code: Option<i32>,
    clean: bool,
    restart_count: u32,
) -> ExitAction {
    // Deliberate exits first — a restore is triggered by a `backup.restore` IPC
    // call, never by `CloseRequested`, so `clean` is false when exit 5 fires.
    if reason == Some("restore_staged") || code == Some(5) {
        return ExitAction::Restore;
    }
    if reason == Some("previous_data_unrecoverable") || code == Some(3) {
        return ExitAction::PreviousDataUnrecoverable;
    }
    if clean {
        return ExitAction::CleanExit;
    }
    if restart_count >= MAX_RESTARTS {
        ExitAction::GaveUp
    } else {
        ExitAction::Retry(BACKOFF_MS[restart_count as usize])
    }
}

/// How a parsed frame should be handled. Determined by `message_type` alone —
/// never the presence of `request_id` (event frames can carry one, e.g.
/// `model.download.progress`).
#[derive(Debug, PartialEq)]
enum FrameKind {
    Correlatable { request_id: String, is_error: bool },
    Event { method: String },
    Passthrough,
}

fn classify_frame(frame: &Value) -> FrameKind {
    let request_id = frame
        .get("request_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    match frame.get("message_type").and_then(Value::as_str) {
        Some("response") => FrameKind::Correlatable {
            request_id,
            is_error: false,
        },
        Some("error") => FrameKind::Correlatable {
            request_id,
            is_error: true,
        },
        Some("event") => FrameKind::Event {
            method: frame
                .pointer("/payload/method")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        },
        _ => FrameKind::Passthrough,
    }
}

/// Everything the shell holds for the running backend. Per-field `Mutex` so the
/// stdout reader locking `pending` never contends with a `child` reap.
pub struct BackendBridge {
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
    pending: Mutex<HashMap<String, oneshot::Sender<Value>>>,
    exit_hint: Mutex<ExitHint>,
    supervisor: Mutex<Supervisor>,
}

impl BackendBridge {
    fn new(child: Child, stdin: ChildStdin) -> Self {
        Self {
            child: Mutex::new(Some(child)),
            stdin: Mutex::new(Some(stdin)),
            pending: Mutex::new(HashMap::new()),
            exit_hint: Mutex::new(ExitHint::default()),
            supervisor: Mutex::new(Supervisor::default()),
        }
    }

    fn write_line(&self, line: &str) -> std::io::Result<()> {
        let mut guard = self.stdin.lock().unwrap();
        let stdin = guard.as_mut().ok_or_else(|| {
            std::io::Error::new(std::io::ErrorKind::BrokenPipe, "backend stdin is closed")
        })?;
        stdin.write_all(line.as_bytes())?;
        stdin.write_all(b"\n")?;
        stdin.flush()
    }

    fn register(&self, request_id: &str) -> oneshot::Receiver<Value> {
        let (tx, rx) = oneshot::channel();
        self.pending
            .lock()
            .unwrap()
            .insert(request_id.to_string(), tx);
        rx
    }

    fn forget(&self, request_id: &str) {
        self.pending.lock().unwrap().remove(request_id);
    }

    /// Drop every pending sender → each waiting `ipc_request` resolves to
    /// `Err(BridgeError::backend_exited())`.
    fn drain_pending(&self) {
        self.pending.lock().unwrap().clear();
    }

    /// Kill the backend child if it is still running. Idempotent.
    pub fn kill(&self) {
        if let Some(mut child) = self.child.lock().unwrap().take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    /// Replace the child + reader thread (crash respawn / restore relaunch /
    /// user "Restart"). Keeps `supervisor` (`restart_count` spans respawns).
    fn respawn(&self, app: &AppHandle) -> std::io::Result<()> {
        let (child, stdin, stdout) = build_child()?;
        *self.child.lock().unwrap() = Some(child);
        *self.stdin.lock().unwrap() = Some(stdin);
        self.pending.lock().unwrap().clear();
        *self.exit_hint.lock().unwrap() = ExitHint::default();

        let app = app.clone();
        std::thread::Builder::new()
            .name("backend-stdout".into())
            .spawn(move || read_stdout(app, stdout))?;
        Ok(())
    }
}

// --------------------------------------------------------------------------
// Spawn
// --------------------------------------------------------------------------

/// `<repo>` — the rag-pipeline checkout root. `CARGO_MANIFEST_DIR` is
/// `<repo>/desktop/src-tauri` at build time; two levels up is the root.
fn repo_root() -> PathBuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let root = manifest_dir.ancestors().nth(2).map(Path::to_path_buf);
    root.unwrap_or(manifest_dir)
}

fn backend_python(root: &Path) -> PathBuf {
    match std::env::var_os("MIRRORMIND_BACKEND_PYTHON") {
        Some(p) => PathBuf::from(p),
        None => root.join(".venv").join("Scripts").join("python.exe"),
    }
}

fn backend_cwd(root: &Path) -> PathBuf {
    match std::env::var_os("MIRRORMIND_BACKEND_CWD") {
        Some(p) => PathBuf::from(p),
        None => root.to_path_buf(),
    }
}

/// The absolute `RAGPIPE_DATA_DIR` the shell passes to the child — the single
/// source of truth for both the spawn env and `perform_restore_swap`.
fn data_dir() -> PathBuf {
    match std::env::var_os("RAGPIPE_DATA_DIR") {
        Some(p) => PathBuf::from(p),
        None => repo_root().join("desktop").join(".dev-data"),
    }
}

fn build_child() -> std::io::Result<(Child, ChildStdin, ChildStdout)> {
    let root = repo_root();
    let python = backend_python(&root);
    let cwd = backend_cwd(&root);
    let dir = data_dir();
    std::fs::create_dir_all(&dir)?;

    eprintln!(
        "[mirrormind] spawning backend: {} -m src.backend.main (cwd={}, RAGPIPE_DATA_DIR={})",
        python.display(),
        cwd.display(),
        dir.display()
    );

    let mut child = Command::new(&python)
        .args(["-m", "src.backend.main"])
        .current_dir(&cwd)
        .env("RAGPIPE_DATA_DIR", &dir)
        .env("PYTHONUNBUFFERED", "1")
        // The pytest conftest guard fails if real Langfuse creds leak in.
        .env("LANGFUSE_PUBLIC_KEY", "")
        .env("LANGFUSE_SECRET_KEY", "")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()?;

    let stdout = child
        .stdout
        .take()
        .expect("child configured with piped stdout");
    let stdin = child
        .stdin
        .take()
        .expect("child configured with piped stdin");
    Ok((child, stdin, stdout))
}

/// Spawn the backend and start the stdout reader thread (initial launch).
pub fn spawn(app: &AppHandle) -> std::io::Result<BackendBridge> {
    let (child, stdin, stdout) = build_child()?;
    let app_for_thread = app.clone();
    std::thread::Builder::new()
        .name("backend-stdout".into())
        .spawn(move || read_stdout(app_for_thread, stdout))?;
    Ok(BackendBridge::new(child, stdin))
}

// --------------------------------------------------------------------------
// stdout reader + supervisor
// --------------------------------------------------------------------------

fn emit_exit(
    app: &AppHandle,
    code: Option<i32>,
    reason: &str,
    snapshot: Option<&str>,
    retry: bool,
) {
    let _ = app.emit(
        "backend:exit",
        json!({ "code": code, "reason": reason, "snapshot_path": snapshot, "will_retry": retry }),
    );
}

fn read_stdout(app: AppHandle, stdout: ChildStdout) {
    let reader = BufReader::new(stdout);
    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(err) => {
                eprintln!("[mirrormind] backend stdout read error: {err}");
                break;
            }
        };
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        match serde_json::from_str::<Value>(trimmed) {
            Ok(frame) => route_frame(&app, frame),
            Err(err) => {
                eprintln!("[mirrormind] backend emitted a non-JSON line ({err}): {trimmed}")
            }
        }
    }

    // stdout closed => the child has exited (or is hung with a closed pipe).
    let code = reap_child_bounded(&app);
    let Some(bridge) = app.try_state::<BackendBridge>() else {
        return;
    };
    bridge.drain_pending();

    let (reason, snapshot) = {
        let hint = bridge.exit_hint.lock().unwrap();
        (hint.reason.clone(), hint.snapshot_path.clone())
    };
    let action = {
        let sup = bridge.supervisor.lock().unwrap();
        decide_on_exit(
            reason.as_deref(),
            code,
            sup.clean_shutdown,
            sup.restart_count,
        )
    };
    eprintln!("[mirrormind] backend exited (code={code:?}, reason={reason:?}, action={action:?})");

    match action {
        ExitAction::CleanExit => emit_exit(&app, code, "clean", None, false),
        ExitAction::PreviousDataUnrecoverable => {
            emit_exit(&app, code, "previous_data_unrecoverable", None, false)
        }
        ExitAction::GaveUp => {
            bridge.supervisor.lock().unwrap().gave_up = true;
            emit_exit(&app, code, "supervisor_gave_up", None, false);
        }
        ExitAction::Retry(ms) => {
            bridge.supervisor.lock().unwrap().restart_count += 1;
            emit_exit(&app, code, "crash", None, true);
            std::thread::sleep(Duration::from_millis(ms));
            if let Err(err) = bridge.respawn(&app) {
                eprintln!("[mirrormind] respawn failed: {err}");
                // The shell can't even start the process — automatic recovery is
                // over; hand it to the "Restart" button.
                bridge.supervisor.lock().unwrap().gave_up = true;
                emit_exit(&app, None, "respawn_failed", None, false);
            }
        }
        ExitAction::Restore => {
            // will_retry: true — the swap is followed by a respawn, so the UI
            // must leave the terminal `exited` phase and let `app.ready` recover.
            emit_exit(&app, code, "restore_staged", snapshot.as_deref(), true);
            let outcome = match snapshot.as_deref() {
                Some(path) => perform_restore_swap(path).and_then(|()| bridge.respawn(&app)),
                None => Err(std::io::Error::new(
                    std::io::ErrorKind::NotFound,
                    "no validated snapshot path",
                )),
            };
            if let Err(err) = outcome {
                eprintln!("[mirrormind] restore failed: {err}");
                bridge.supervisor.lock().unwrap().gave_up = true;
                emit_exit(&app, None, "restore_failed", None, false);
            }
        }
    }
}

/// Route one parsed frame to a pending `ipc_request` waiter or the webview.
fn route_frame(app: &AppHandle, frame: Value) {
    match classify_frame(&frame) {
        FrameKind::Correlatable {
            request_id,
            is_error,
        } => {
            let waiter = app
                .try_state::<BackendBridge>()
                .and_then(|b| b.pending.lock().unwrap().remove(&request_id));
            match waiter {
                Some(tx) => {
                    let _ = tx.send(frame);
                }
                None if is_error => {
                    let _ = app.emit("backend:error", frame);
                }
                None => {
                    let _ = app.emit("backend:message", frame);
                }
            }
        }
        FrameKind::Event { method } => {
            if let Some(bridge) = app.try_state::<BackendBridge>() {
                bridge.exit_hint.lock().unwrap().observe(&method, &frame);
                if method == "app.ready" {
                    let mut sup = bridge.supervisor.lock().unwrap();
                    sup.restart_count = 0;
                    sup.gave_up = false;
                }
            }
            let _ = app.emit("backend:message", frame);
        }
        FrameKind::Passthrough => {
            let _ = app.emit("backend:message", frame);
        }
    }
}

/// Reap the child, bounded by `REAP_BOUND` — then hard-kill and return `None`
/// (treated as a crash). Guards the pathological "stdout closed, process alive".
fn reap_child_bounded(app: &AppHandle) -> Option<i32> {
    let bridge = app.try_state::<BackendBridge>()?;
    let deadline = Instant::now() + REAP_BOUND;
    loop {
        {
            let mut guard = bridge.child.lock().unwrap();
            let child = guard.as_mut()?;
            match child.try_wait() {
                Ok(Some(status)) => {
                    let code = status.code();
                    *guard = None;
                    return code;
                }
                Ok(None) if Instant::now() >= deadline => {
                    let _ = child.kill();
                    let _ = child.wait();
                    *guard = None;
                    return None;
                }
                Ok(None) => {}
                Err(_) => {
                    *guard = None;
                    return None;
                }
            }
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// Move `snapshot_path` onto the live session DB (backend is fully down, so no
/// open connections). `rename` is atomic on the same volume; fall back to
/// `copy` + `remove` on any error.
fn perform_restore_swap(snapshot_path: &str) -> std::io::Result<()> {
    let dir = data_dir();
    let live = dir.join("session.db");
    if std::fs::rename(snapshot_path, &live).is_err() {
        std::fs::copy(snapshot_path, &live)?;
        let _ = std::fs::remove_file(snapshot_path);
    }
    let _ = std::fs::remove_file(dir.join("session.db-wal"));
    let _ = std::fs::remove_file(dir.join("session.db-shm"));
    Ok(())
}

// --------------------------------------------------------------------------
// ipc_request / restart_backend
// --------------------------------------------------------------------------

#[derive(serde::Serialize)]
pub struct BridgeError {
    /// "timeout" | "backend_exited" | "backend_unavailable" | "transport"
    kind: String,
    message: String,
}

impl BridgeError {
    fn new(kind: &str, message: impl Into<String>) -> Self {
        Self {
            kind: kind.into(),
            message: message.into(),
        }
    }
    fn timeout() -> Self {
        Self::new("timeout", "the backend did not respond in time")
    }
    fn backend_exited() -> Self {
        Self::new(
            "backend_exited",
            "the backend exited while the request was pending",
        )
    }
    fn backend_unavailable(message: impl Into<String>) -> Self {
        Self::new("backend_unavailable", message)
    }
    fn transport(message: impl Into<String>) -> Self {
        Self::new("transport", message)
    }
}

/// The single request/response entry point. The frontend builds the envelope
/// (`desktop/src/ipc/bridge.ts`); this correlates the reply by `request_id`.
#[tauri::command]
pub async fn ipc_request(
    app: AppHandle,
    envelope: Value,
    timeout_ms: u64,
) -> Result<Value, BridgeError> {
    let request_id = envelope
        .get("request_id")
        .and_then(Value::as_str)
        .ok_or_else(|| BridgeError::transport("envelope is missing a string request_id"))?
        .to_string();
    let line =
        serde_json::to_string(&envelope).map_err(|e| BridgeError::transport(e.to_string()))?;

    let rx = {
        let bridge = app
            .try_state::<BackendBridge>()
            .ok_or_else(|| BridgeError::backend_unavailable("the backend is not running"))?;
        let rx = bridge.register(&request_id);
        if let Err(err) = bridge.write_line(&line) {
            bridge.forget(&request_id);
            return Err(BridgeError::backend_unavailable(err.to_string()));
        }
        rx
    };

    let dur = if timeout_ms == 0 {
        REQUEST_CEILING
    } else {
        Duration::from_millis(timeout_ms).min(REQUEST_CEILING)
    };

    match tokio::time::timeout(dur, rx).await {
        Ok(Ok(frame)) => Ok(frame),
        Ok(Err(_)) => Err(BridgeError::backend_exited()),
        Err(_) => {
            if let Some(bridge) = app.try_state::<BackendBridge>() {
                bridge.forget(&request_id);
            }
            Err(BridgeError::timeout())
        }
    }
}

/// User-initiated recovery after the supervisor gave up (the fe.5 "Restart"
/// banner button). Gated: only reachable once `gave_up`.
#[tauri::command]
pub fn restart_backend(app: AppHandle) -> Result<(), String> {
    let bridge = app
        .try_state::<BackendBridge>()
        .ok_or("backend bridge missing")?;
    {
        let mut sup = bridge.supervisor.lock().unwrap();
        if !sup.gave_up {
            return Err("the backend is still being supervised".into());
        }
        *sup = Supervisor::default();
    }
    bridge.kill();
    // Tell the UI a relaunch is in flight so a stale terminal banner clears and
    // the next `app.ready` is allowed to promote `phase` back to "ready".
    emit_exit(&app, None, "manual_restart", None, true);
    match bridge.respawn(&app) {
        Ok(()) => Ok(()),
        Err(err) => {
            bridge.supervisor.lock().unwrap().gave_up = true;
            emit_exit(&app, None, "respawn_failed", None, false);
            Err(err.to_string())
        }
    }
}

// --------------------------------------------------------------------------
// Graceful shutdown (window close)
// --------------------------------------------------------------------------

fn now_rfc3339() -> String {
    use time::format_description::well_known::Rfc3339;
    time::OffsetDateTime::now_utc()
        .format(&Rfc3339)
        .unwrap_or_else(|_| "1970-01-01T00:00:00Z".into())
}

fn shutdown_envelope() -> String {
    json!({
        "version": IPC_VERSION,
        "message_type": "request",
        "request_id": "be-shutdown",
        "timestamp": now_rfc3339(),
        "payload": { "method": "app.shutdown", "params": {} },
    })
    .to_string()
}

/// Send `app.shutdown`, wait up to `SHUTDOWN_GRACE` for the backend to exit
/// cleanly, then hard-kill. Spawned on Tauri's async runtime from the window
/// `CloseRequested` handler; `app.exit(0)` follows.
pub async fn graceful_shutdown(app: &AppHandle) {
    if let Some(bridge) = app.try_state::<BackendBridge>() {
        // Mark the exit clean BEFORE the backend can act on the request, so the
        // reader's EOF handler does not treat it as a crash.
        bridge.supervisor.lock().unwrap().clean_shutdown = true;
        let _ = bridge.write_line(&shutdown_envelope());
    }

    let start = Instant::now();
    loop {
        let exited = match app.try_state::<BackendBridge>() {
            Some(bridge) => bridge
                .child
                .lock()
                .unwrap()
                .as_mut()
                .map(|c| matches!(c.try_wait(), Ok(Some(_))))
                .unwrap_or(true),
            None => true,
        };
        if exited || start.elapsed() >= SHUTDOWN_GRACE {
            break;
        }
        tokio::time::sleep(Duration::from_millis(150)).await;
    }

    if let Some(bridge) = app.try_state::<BackendBridge>() {
        bridge.kill();
    }
}

// --------------------------------------------------------------------------
// tests
// --------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classify_by_message_type_not_request_id() {
        let progress = json!({
            "message_type": "event", "request_id": "req-7",
            "payload": { "method": "model.download.progress", "params": {} }
        });
        assert_eq!(
            classify_frame(&progress),
            FrameKind::Event {
                method: "model.download.progress".into()
            }
        );

        let resp = json!({ "message_type": "response", "request_id": "req-7", "payload": {} });
        assert_eq!(
            classify_frame(&resp),
            FrameKind::Correlatable {
                request_id: "req-7".into(),
                is_error: false
            }
        );

        assert_eq!(
            classify_frame(&json!({ "junk": 1 })),
            FrameKind::Passthrough
        );
    }

    #[test]
    fn exit_hint_tracks_the_last_lifecycle_event() {
        let mut hint = ExitHint::default();
        hint.observe(
            "app.restore_staged",
            &json!({ "payload": { "params": { "validated_snapshot_path": "C:\\x.db" } } }),
        );
        assert_eq!(hint.reason.as_deref(), Some("restore_staged"));
        assert_eq!(hint.snapshot_path.as_deref(), Some("C:\\x.db"));

        hint.observe("app.reminders_pending", &json!({ "payload": {} }));
        assert_eq!(hint.reason.as_deref(), Some("restore_staged")); // unchanged
    }

    #[test]
    fn decide_deliberate_exits_before_clean() {
        // restore wins even when clean_shutdown happens to be set
        assert_eq!(
            decide_on_exit(Some("restore_staged"), Some(0), true, 0),
            ExitAction::Restore
        );
        assert_eq!(decide_on_exit(None, Some(5), false, 0), ExitAction::Restore);
        assert_eq!(
            decide_on_exit(Some("previous_data_unrecoverable"), None, false, 0),
            ExitAction::PreviousDataUnrecoverable
        );
        assert_eq!(
            decide_on_exit(None, Some(3), false, 0),
            ExitAction::PreviousDataUnrecoverable
        );
    }

    #[test]
    fn decide_clean_then_crash_backoff() {
        assert_eq!(
            decide_on_exit(None, Some(0), true, 0),
            ExitAction::CleanExit
        );
        assert_eq!(
            decide_on_exit(None, Some(1), false, 0),
            ExitAction::Retry(1000)
        );
        assert_eq!(
            decide_on_exit(None, Some(1), false, 1),
            ExitAction::Retry(2000)
        );
        assert_eq!(
            decide_on_exit(None, Some(1), false, 2),
            ExitAction::Retry(4000)
        );
        assert_eq!(decide_on_exit(None, Some(1), false, 3), ExitAction::GaveUp);
        assert_eq!(decide_on_exit(None, None, false, 9), ExitAction::GaveUp);
    }

    #[test]
    fn backoff_table_covers_every_retry() {
        assert_eq!(BACKOFF_MS.len(), MAX_RESTARTS as usize);
        assert!(BACKOFF_MS.iter().all(|&ms| ms > 0));
    }

    #[test]
    fn shutdown_envelope_is_a_full_ipc_request() {
        let env: Value = serde_json::from_str(&shutdown_envelope()).unwrap();
        assert_eq!(env["version"], IPC_VERSION);
        assert_eq!(env["message_type"], "request");
        assert_eq!(env["request_id"], "be-shutdown");
        assert!(env["timestamp"].as_str().is_some_and(|s| !s.is_empty()));
        assert_eq!(env["payload"]["method"], "app.shutdown");
    }

    #[test]
    fn now_rfc3339_is_parseable() {
        let ts = now_rfc3339();
        assert!(
            time::OffsetDateTime::parse(&ts, &time::format_description::well_known::Rfc3339)
                .is_ok()
        );
    }
}
