import { Navigate, Route, Routes } from "react-router-dom";

import { Chat } from "./routes/Chat";
import { FirstRun } from "./routes/FirstRun";
import { Loading } from "./routes/Loading";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Loading />} />
      <Route path="/first-run" element={<FirstRun />} />
      <Route path="/chat" element={<Chat />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
