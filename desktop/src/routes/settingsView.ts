/**
 * Pure helpers for the Settings screen (Phase 3 Step 3.4). No React, no store —
 * unit-tested directly. The screen is one `/settings` route; the active panel is
 * a `?tab=` URL param.
 */

export const SETTINGS_TABS = ["general", "models", "data", "backup", "diagnostics"] as const;
export type SettingsTab = (typeof SETTINGS_TABS)[number];

export const SETTINGS_TAB_LABELS: Record<SettingsTab, string> = {
  general: "General",
  models: "Models",
  data: "Data & Privacy",
  backup: "Backup & Recovery",
  diagnostics: "Diagnostics",
};

/** The active panel for a `?tab=` value — anything unknown falls back to "general". */
export function activeTab(raw: string | null | undefined): SettingsTab {
  return (SETTINGS_TABS as readonly string[]).includes(raw ?? "")
    ? (raw as SettingsTab)
    : "general";
}

const EXPORT_BADGE_DAYS = 30; // mirrors src/features/data_admin.py::_EXPORT_BADGE_DAYS

/** The nav-rail export nudge: never exported, or last export > 30 days ago. */
export function needsExportBadge(lastExportedAt: string | null, nowIso: string): boolean {
  if (!lastExportedAt) return true;
  const last = Date.parse(lastExportedAt);
  const now = Date.parse(nowIso);
  if (Number.isNaN(last) || Number.isNaN(now)) return true;
  return (now - last) / 86_400_000 > EXPORT_BADGE_DAYS;
}

// -- General panel form validation ---------------------------------------

export const IDLE_MIN = 1;
export const IDLE_MAX = 1440;

export function isValidIdle(n: number): boolean {
  return Number.isInteger(n) && n >= IDLE_MIN && n <= IDLE_MAX;
}

export function isValidTime(s: string): boolean {
  return /^([01]\d|2[0-3]):[0-5]\d$/.test(s);
}
