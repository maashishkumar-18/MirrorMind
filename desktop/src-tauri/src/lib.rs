//! MirrorMind desktop shell (Tauri v2) — Phase 3 Step 3.1 (fe.3 / fe.7).
//!
//! Spawns the Python backend sidecar, exposes `ipc_request` (the stdio<->invoke
//! bridge) + `restart_backend`, supervises the backend (crash → backoff
//! restart), enforces a single instance, and does a graceful `app.shutdown`
//! handshake on window close.

mod backend;

use tauri::{Manager, RunEvent, WindowEvent};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // Must be the first plugin: a second launch focuses the running window
        // and exits, rather than spawning a second backend.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        // Settings → Data & Privacy / Diagnostics (Step 3.4): the native save
        // dialog for exports + the redacted report, and reveal-in-folder /
        // open-mailto for "Report a problem".
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
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
        .invoke_handler(tauri::generate_handler![
            backend::ipc_request,
            backend::restart_backend
        ])
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
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
            // Safety net: any exit path that skips the window handler still kills
            // the child. `kill()` is idempotent.
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                if let Some(bridge) = app_handle.try_state::<backend::BackendBridge>() {
                    bridge.kill();
                }
            }
        });
}
