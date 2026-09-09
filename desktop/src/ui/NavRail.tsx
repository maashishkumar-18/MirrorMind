import { NavLink, useLocation } from "react-router-dom";

import { needsExportBadge } from "../routes/settingsView";
import { useBackendStore } from "../store/backend";
import { useSettingsStore } from "../store/settings";

/** The reachable destinations. Settings joined in Step 3.4. */
export const NAV_ITEMS = [
  { to: "/chat", label: "Chat", glyph: "💬" },
  { to: "/reminders", label: "Reminders", glyph: "⏰" },
  { to: "/todos", label: "To-dos", glyph: "✓" },
  { to: "/meetings", label: "Meetings", glyph: "📝" },
  { to: "/schedule", label: "Schedule", glyph: "📅" },
  { to: "/settings", label: "Settings", glyph: "⚙️" },
] as const;

/**
 * Persistent left sidebar (Phase 3 Step 3.3). Hidden on the pre-app routes
 * (`/`, `/first-run`) and while the backend has not reached a usable phase —
 * the feature routes all sit behind `<RequireModel>` anyway. A dot on Settings
 * flags a stale / never-done data export (Step 3.4).
 */
export function NavRail() {
  const phase = useBackendStore((s) => s.phase);
  const lastExportedAt = useSettingsStore((s) => s.lastExportedAt);
  const { pathname } = useLocation();

  if (pathname === "/" || pathname === "/first-run") return null;
  if (phase === "starting" || phase === "exited") return null;

  const exportBadge = needsExportBadge(lastExportedAt, new Date().toISOString());

  return (
    <nav className="nav-rail" aria-label="Primary">
      {NAV_ITEMS.map((item) => (
        <NavLink key={item.to} to={item.to} className="nav-rail-link">
          <span className="nav-rail-glyph" aria-hidden="true">
            {item.glyph}
          </span>
          <span>{item.label}</span>
          {item.to === "/settings" && exportBadge && (
            <span className="nav-rail-badge" aria-label="Data export recommended">
              ●
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  );
}
