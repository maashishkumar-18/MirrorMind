/**
 * Pure helpers for Settings → Diagnostics (Phase 3 Step 3.4). No React — the
 * `.tsx` panel is a thin wrapper; the formatting lives here and is unit-tested.
 */

export const LOG_LEVELS = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR"] as const;
export type LogLevel = (typeof LOG_LEVELS)[number];

/** The `level` param sent to `diagnostics.logs` ("ALL" → no filter → null). */
export function levelParam(level: LogLevel): string | null {
  return level === "ALL" ? null : level;
}

export interface Distribution {
  high: number;
  medium: number;
  low: number;
  none: number;
}

/** Each bucket as a 0–100 width (of the total sample). */
export function barWidths(d: Distribution): Distribution {
  const total = d.high + d.medium + d.low + d.none;
  const pct = (n: number) => (total === 0 ? 0 : Math.round((n / total) * 100));
  return { high: pct(d.high), medium: pct(d.medium), low: pct(d.low), none: pct(d.none) };
}

export function formatPct(x: number | null | undefined): string {
  return x == null ? "not tracked yet" : `${Math.round(x * 100)}%`;
}

export function formatMs(x: number | null | undefined): string {
  return x == null ? "not tracked yet" : `${Math.round(x)} ms`;
}

/** The pre-filled mailto: body — mailto cannot attach, so it tells the user to. */
export function reportMailto(savedPath: string): string {
  const body = [
    "Describe what happened:",
    "",
    "",
    "---",
    `Please attach the diagnostics report you just saved: ${savedPath}`,
    "(It has been redacted — no message content, just events and errors.)",
  ].join("\n");
  return `mailto:?subject=${encodeURIComponent("MirrorMind problem report")}&body=${encodeURIComponent(body)}`;
}
