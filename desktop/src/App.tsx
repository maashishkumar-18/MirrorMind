import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { startBackendBridge } from "./bootstrap";
import { Chat } from "./routes/Chat";
import { FirstRun } from "./routes/FirstRun";
import { Loading } from "./routes/Loading";
import { RequireModel } from "./ui/RequireModel";
import { RootLayout } from "./ui/RootLayout";

export default function App() {
  useEffect(() => {
    // Wire the backend event listeners once, after mount. Running Tauri IPC at
    // module-eval time races the webview's IPC init.
    let teardown: (() => void) | undefined;
    let cancelled = false;
    void startBackendBridge().then((fn) => {
      if (cancelled) fn();
      else teardown = fn;
    });
    return () => {
      cancelled = true;
      teardown?.();
    };
  }, []);

  return (
    <Routes>
      <Route element={<RootLayout />}>
        <Route path="/" element={<Loading />} />
        <Route path="/first-run" element={<FirstRun />} />
        <Route element={<RequireModel />}>
          <Route path="/chat" element={<Chat />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
