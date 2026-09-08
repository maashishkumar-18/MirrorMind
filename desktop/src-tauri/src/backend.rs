//! Python backend sidecar + the stdio<->invoke bridge (Phase 3 Step 3.1-fe.3).
//!
//! - Spawns `python -m src.backend.main` (the newline-delimited JSON `IPCEnvelope`
//!   sidecar) and reads its stdout line by line.
//! - `response` / `error` frames whose `request_id` matches a pending
//!   `ipc_request` complete that call's oneshot; every other frame (events —
//!   including `model.download.progress`, which carries the originating
//!   `request_id` — and unmatched frames) is forwarded to the webview as a
//!   `backend:message` Tauri event, with unattributed errors on `backend:error`.
//! - On child exit: every still-pending `ipc_request` is failed and
//!   `backend:exit` is emitted with `{ code, reason, snapshot_path }` (the
//!   reason/path come from the last lifecycle event seen — exit 3 / exit 5).
//! - Window close sends `app.shutdown`, waits up to 10 s for a clean
//!   `ShutdownCoordinator` teardown, then hard-kills.
//!
//! The process supervisor (backoff restart) and acting on exit 3 / exit 5 are
//! fe.7 / fe.5-6 — fe.3 only surfaces them.

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
/// hard kill (worst case: `SessionWorker.stop` 15 s + `SchedulerThread.stop` 5 s,
/// but only when an inference is mid-flight; idle teardown is ~instant).
const SHUTDOWN_GRACE: Duration = Duration::from_secs(10);
/// Hard ceiling on any single `ipc_request`, regardless of the caller's timeout.
const REQUEST_CEILING: Duration = Duration::from_secs(900);

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

/// How a parsed frame should be handled. Determined by `message_type` alone —
/// never the presence of `request_id` (event frames can carry one, e.g.
/// `model.download.progress`).
#[derive(Debug, PartialEq)]
enum FrameKind {
    /// `response` / `error` — try to complete the pending call `request_id`.
    Correlatable { request_id: String, is_error: bool },
    /// a server-initiated `event` — forward to the webview, note lifecycle hints
    Event { method: String },
    /// anything else — forward to the webview for debugging
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
}

impl BackendBridge {
    fn new(child: Child, stdin: ChildStdin) -> Self {
        Self {
            child: Mutex::new(Some(child)),
            stdin: Mutex::new(Some(stdin)),
            pending: Mutex::new(HashMap::new()),
            exit_hint: Mutex::new(ExitHint::default()),
        }
    }

    /// Write one envelope line to the backend's stdin. `Err(BrokenPipe)` once the
    /// child is gone.
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
}

// --------------------------------------------------------------------------
// Spawn
// --------------------------------------------------------------------------

/// `<repo>` — the rag-pipeline checkout root. `CARGO_MANIFEST_DIR` is
/// `<repo>/desktop/src-tauri` at build time; two levels up is the root. A
/// dev-only default; `MIRRORMIND_BACKEND_CWD` overrides it.
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

/// Spawn the backend and start the stdout reader thread.
pub fn spawn(app: &AppHandle) -> std::io::Result<BackendBridge> {
    let root = repo_root();
    let python = backend_python(&root);
    let cwd = backend_cwd(&root);

    // Isolated dev state dir, passed as an absolute path (root is absolute) so
    // it is unambiguous regardless of the child's working directory.
    let data_dir = root.join("desktop").join(".dev-data");
    std::fs::create_dir_all(&data_dir)?;

    eprintln!(
        "[mirrormind] spawning backend: {} -m src.backend.main (cwd={}, RAGPIPE_DATA_DIR={})",
        python.display(),
        cwd.display(),
        data_dir.display()
    );

    let mut child = Command::new(&python)
        .args(["-m", "src.backend.main"])
        .current_dir(&cwd)
        .env("RAGPIPE_DATA_DIR", &data_dir)
        .env("PYTHONUNBUFFERED", "1")
        // The pytest conftest guard fails if real Langfuse creds leak in; the
        // shell has no observability backend in dev.
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

    let app_for_thread = app.clone();
    std::thread::Builder::new()
        .name("backend-stdout".into())
        .spawn(move || read_stdout(app_for_thread, stdout))?;

    Ok(BackendBridge::new(child, stdin))
}

// --------------------------------------------------------------------------
// stdout reader
// --------------------------------------------------------------------------

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

    // stdout closed => the child has exited (or is about to).
    let code = reap_child(&app);
    let (reason, snapshot_path) = match app.try_state::<BackendBridge>() {
        Some(bridge) => {
            bridge.drain_pending();
            let hint = bridge.exit_hint.lock().unwrap();
            (hint.reason.clone(), hint.snapshot_path.clone())
        }
        None => (None, None),
    };
    eprintln!("[mirrormind] backend exited (code={code:?}, reason={reason:?})");
    let _ = app.emit(
        "backend:exit",
        json!({ "code": code, "reason": reason, "snapshot_path": snapshot_path }),
    );
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
                // an error the frontend never asked for (request_id "-" or a
                // stale id) — surface it; a stray response goes to the log path
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
            }
            let _ = app.emit("backend:message", frame);
        }
        FrameKind::Passthrough => {
            let _ = app.emit("backend:message", frame);
        }
    }
}

fn reap_child(app: &AppHandle) -> Option<i32> {
    app.try_state::<BackendBridge>()
        .and_then(|b| b.child.lock().unwrap().take())
        .and_then(|mut child| child.wait().ok())
        .and_then(|status| status.code())
}

// --------------------------------------------------------------------------
// ipc_request
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
/// A well-formed `error` frame comes back as `Ok(<error envelope>)` — only
/// transport failures are `Err`.
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

    // Register BEFORE writing so a fast reply can never arrive unregistered.
    // The `State` borrow is confined to this block — never held across `.await`.
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
        // Ignore the error: the backend may already be gone.
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

        let err = json!({ "message_type": "error", "request_id": "-", "payload": { "code": "x" } });
        assert_eq!(
            classify_frame(&err),
            FrameKind::Correlatable {
                request_id: "-".into(),
                is_error: true
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
        assert_eq!(hint.reason, None);

        hint.observe(
            "app.restore_staged",
            &json!({ "payload": { "params": { "validated_snapshot_path": "C:\\x.db" } } }),
        );
        assert_eq!(hint.reason.as_deref(), Some("restore_staged"));
        assert_eq!(hint.snapshot_path.as_deref(), Some("C:\\x.db"));

        hint.observe("app.reminders_pending", &json!({ "payload": {} }));
        assert_eq!(hint.reason.as_deref(), Some("restore_staged")); // unchanged

        let mut hint2 = ExitHint::default();
        hint2.observe("app.previous_data_unrecoverable", &json!({ "payload": {} }));
        assert_eq!(hint2.reason.as_deref(), Some("previous_data_unrecoverable"));
        assert_eq!(hint2.snapshot_path, None);
    }

    #[test]
    fn shutdown_envelope_is_a_full_ipc_request() {
        let env: Value = serde_json::from_str(&shutdown_envelope()).unwrap();
        assert_eq!(env["version"], IPC_VERSION);
        assert_eq!(env["message_type"], "request");
        assert_eq!(env["request_id"], "be-shutdown");
        assert!(env["timestamp"].as_str().is_some_and(|s| !s.is_empty()));
        assert_eq!(env["payload"]["method"], "app.shutdown");
        assert!(env["payload"]["params"].is_object());
    }

    #[test]
    fn now_rfc3339_is_non_empty_and_parseable() {
        let ts = now_rfc3339();
        assert!(
            time::OffsetDateTime::parse(&ts, &time::format_description::well_known::Rfc3339)
                .is_ok()
        );
    }
}
