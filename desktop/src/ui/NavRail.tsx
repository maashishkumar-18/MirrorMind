import { NavLink, useLocation } from "react-router-dom";

import { useBackendStore } from "../store/backend";

/** The five reachable destinations (Settings joins in Step 3.4). */
export const NAV_ITEMS = [
  { to: "/chat", label: "Chat", glyph: "💬" },
  { to: "/reminders", label: "Reminders", glyph: "⏰" },
  { to: "/todos", label: "To-dos", glyph: "✓" },
  { to: "/meetings", label: "Meetings", glyph: "📝" },
  { to: "/schedule", label: "Schedule", glyph: "📅" },
] as const;

/**
 * Persistent left sidebar (Phase 3 Step 3.3). Hidden on the pre-app routes
 * (`/`, `/first-run`) and while the backend has not reached a usable phase —
 * the feature routes all sit behind `<RequireModel>` anyway.
 */
export function NavRail() {
  const phase = useBackendStore((s) => s.phase);
  const { pathname } = useLocation();

  if (pathname === "/" || pathname === "/first-run") return null;
  if (phase === "starting" || phase === "exited") return null;

  return (
    <nav className="nav-rail" aria-label="Primary">
      {NAV_ITEMS.map((item) => (
        <NavLink key={item.to} to={item.to} className="nav-rail-link">
          <span className="nav-rail-glyph" aria-hidden="true">
            {item.glyph}
          </span>
          <span>{item.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}
