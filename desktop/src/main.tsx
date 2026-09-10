import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import { useBackendStore } from "./store/backend";
import { useModelStore } from "./store/model";
import { useReminderStore } from "./store/reminders";
import "./styles.css";

// E2E seam (Phase 4 Step 4.1): only active when the Tauri IPC shim is present
// (it sets `__E2E_BRIDGE_PORT__`). Lets the Playwright harness poll store state.
if (typeof window !== "undefined" && "__E2E_BRIDGE_PORT__" in window) {
  (window as unknown as { __MM_STORES__: unknown }).__MM_STORES__ = {
    backend: useBackendStore,
    model: useModelStore,
    reminders: useReminderStore,
  };
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
