import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { startBackendBridge } from "./bootstrap";
import { Chat } from "./routes/Chat";
import { FirstRun } from "./routes/FirstRun";
import { Loading } from "./routes/Loading";

export default function App() {
  useEffect(() => {
    // Wire the backend:* Tauri event listeners once, after mount — running
    // Tauri IPC calls at module-eval time races the webview's IPC init.
    void startBackendBridge();
  }, []);

  return (
    <Routes>
      <Route path="/" element={<Loading />} />
      <Route path="/first-run" element={<FirstRun />} />
      <Route path="/chat" element={<Chat />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
