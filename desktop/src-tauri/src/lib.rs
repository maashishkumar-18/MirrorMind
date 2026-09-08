//! MirrorMind desktop shell (Tauri v2) — Phase 3 Step 3.1-fe.1.
//!
//! For now the shell only launches the Python backend sidecar and forwards its
//! stdout envelopes to the webview. The typed stdio<->invoke bridge (fe.3), the
//! IPC client (fe.4), and the process supervisor (fe.7) build on this.

mod backend;

use tauri::{Manager, RunEvent};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let handle = app.handle().clone();
            match backend::spawn(&handle) {
                Ok(process) => {
                    app.manage(process);
                }
                Err(err) => eprintln!("[mirrormind] failed to spawn backend: {err}"),
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the MirrorMind shell")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                if let Some(process) = app_handle.try_state::<backend::BackendProcess>() {
                    process.kill();
                }
            }
        });
}
