import { Outlet } from "react-router-dom";

import { AppGate } from "./AppGate";
import { Banner } from "./Banner";

/**
 * Hosts the degraded-mode banner and the full-screen gate above every route
 * (Phase 3 Step 3.1-fe.5). The banner is a flex child whose height animates
 * 0 ↔ 2.25rem, so `<main>` slides rather than being obscured.
 */
export function RootLayout() {
  return (
    <div className="root-layout">
      <Banner />
      <main className="app-main">
        <Outlet />
      </main>
      <AppGate />
    </div>
  );
}
