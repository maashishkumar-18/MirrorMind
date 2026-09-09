import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { startBackendBridge } from "./bootstrap";
import { Chat } from "./routes/Chat";
import { FirstRun } from "./routes/FirstRun";
import { Loading } from "./routes/Loading";
import { Meetings } from "./routes/Meetings";
import { Reminders } from "./routes/Reminders";
import { Schedule } from "./routes/Schedule";
import { Settings } from "./routes/Settings";
import { Todos } from "./routes/Todos";
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
          <Route path="/reminders" element={<Reminders />} />
          <Route path="/todos" element={<Todos />} />
          <Route path="/meetings" element={<Meetings />} />
          <Route path="/schedule" element={<Schedule />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
