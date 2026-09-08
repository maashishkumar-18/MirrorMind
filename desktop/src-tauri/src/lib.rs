//! MirrorMind desktop shell (Tauri v2) — Phase 3 Step 3.1-fe.3.
//!
//! Spawns the Python backend sidecar, exposes the `ipc_request` command (the
//! typed stdio<->invoke bridge), and does a graceful `app.shutdown` handshake on
//! window close. The typed zod IPC client (fe.4) and the process supervisor
//! (fe.7) build on this.

mod backend;

use tauri::{Manager, RunEvent, WindowEvent};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let handle = app.handle().clone();
            match backend::spawn(&handle) {
                Ok(bridge) => {
                    app.manage(bridge);
                }
                Err(err) => eprintln!("[mirrormind] failed to spawn backend: {err}"),
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend::ipc_request])
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // Hide immediately (UX feels instant), then run the backend's
                // ShutdownCoordinator teardown before actually exiting.
                api.prevent_close();
                let _ = window.hide();
                let app = window.app_handle().clone();
                tauri::async_runtime::spawn(async move {
                    backend::graceful_shutdown(&app).await;
                    app.exit(0);
                });
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building the MirrorMind shell")
        .run(|app_handle, event| {
            // Safety net: any exit path that skips the window handler (or where
            // the grace period elapsed) still kills the child. `kill()` is
            // idempotent.
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                if let Some(bridge) = app_handle.try_state::<backend::BackendBridge>() {
                    bridge.kill();
                }
            }
        });
}
