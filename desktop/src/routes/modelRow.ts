/**
 * Pure helpers for the first-launch model flow (Phase 3 Step 3.1-fe.6).
 * No React, no store — unit-tested directly.
 */

export type RowAction = "download" | "downloading" | "activate" | "active";

/** Map a `model.status` value to the action a catalog row offers. */
export function rowAction(status: string | undefined): RowAction {
  switch (status) {
    case "downloading":
      return "downloading";
    case "available":
      return "activate";
    case "active":
      return "active";
    default: // "not_installed" | undefined
      return "download";
  }
}

export type GuardDecision = "loading" | "pass" | "first-run";

/**
 * What a route guard should do for a given backend phase + setup flag.
 * `starting` → a neutral loading screen (a returning user never flashes
 * `/first-run`); `degraded` / `exited` → let the route render (the fe.5 banner /
 * gate covers it, and `model.*` calls fail in degraded mode anyway).
 */
export function guardDecision(phase: string, modelSetupRequired: boolean): GuardDecision {
  if (phase === "starting") return "loading";
  if (phase === "degraded" || phase === "exited") return "pass";
  return modelSetupRequired ? "first-run" : "pass";
}

export function formatBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  return `${Math.round(n / 1e3)} KB`;
}

export function formatSpeed(mbps: number): string {
  return `${mbps.toFixed(1)} MB/s`;
}

export function formatEta(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `~${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `~${m}m ${s % 60}s` : `~${m}m`;
  const h = Math.floor(m / 60);
  return m % 60 ? `~${h}h ${m % 60}m` : `~${h}h`;
}
