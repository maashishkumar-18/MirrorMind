import { Outlet } from "react-router-dom";

import { AppGate } from "./AppGate";
import { Banner } from "./Banner";
import { NavRail } from "./NavRail";

/**
 * Hosts the degraded-mode banner and the full-screen gate above every route
 * (Phase 3 Step 3.1-fe.5), plus the persistent nav rail (Step 3.3). The banner
 * is a flex child whose height animates 0 ↔ 2.25rem, so the body slides rather
 * than being obscured; the rail + `<main>` share the row below it.
 */
export function RootLayout() {
  return (
    <div className="root-layout">
      <Banner />
      <div className="root-body">
        <NavRail />
        <main className="app-main">
          <Outlet />
        </main>
      </div>
      <AppGate />
    </div>
  );
}
