//! Python backend sidecar management (Phase 3 Step 3.1-fe.1).
//!
//! Spawns `python -m src.backend.main` (the stdio newline-delimited JSON
//! `IPCEnvelope` sidecar), reads its stdout line by line, and re-emits every
//! parsed envelope to the webview as a `backend:message` event. On child
//! exit it emits `backend:exit` with the exit code.
//!
//! fe.1 scope: passthrough only. There is **no** request/response correlation
//! and **no** `app.shutdown` handshake yet — window close just kills the child
//! (`ShutdownCoordinator` teardown is wired in fe.3). The Rust stdio<->invoke
//! bridge and the process supervisor land in fe.3 / fe.7.

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdout, Command, Stdio};
use std::sync::Mutex;

use tauri::{AppHandle, Emitter, Manager};

/// Managed Tauri state: the handle to the running backend child.
pub struct BackendProcess {
    child: Mutex<Option<Child>>,
}

impl BackendProcess {
    /// Kill the backend child if it is still running. Idempotent.
    pub fn kill(&self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

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
pub fn spawn(app: &AppHandle) -> std::io::Result<BackendProcess> {
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

    let app_for_thread = app.clone();
    std::thread::Builder::new()
        .name("backend-stdout".into())
        .spawn(move || read_stdout(app_for_thread, stdout))?;

    Ok(BackendProcess {
        child: Mutex::new(Some(child)),
    })
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
        match serde_json::from_str::<serde_json::Value>(trimmed) {
            Ok(value) => {
                let _ = app.emit("backend:message", value);
            }
            Err(err) => {
                eprintln!("[mirrormind] backend emitted a non-JSON line ({err}): {trimmed}");
            }
        }
    }

    // stdout closed => the child has exited (or is about to). Reap it for the code.
    let code = app
        .try_state::<BackendProcess>()
        .and_then(|state| {
            let taken = state.child.lock().ok().and_then(|mut guard| guard.take());
            taken.and_then(|mut child| child.wait().ok())
        })
        .and_then(|status| status.code());

    eprintln!("[mirrormind] backend exited (code={code:?})");
    let _ = app.emit("backend:exit", code);
}
