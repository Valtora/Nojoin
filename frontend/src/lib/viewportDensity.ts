import type { DensityPreference } from "./appearance";

export const DESKTOP_BREAKPOINT = 1024;
export const COMPACT_DESKTOP_MAX_WIDTH = 1920;
export const COMPACT_DESKTOP_MAX_HEIGHT = 1080;

export type ViewportDensity = "comfortable" | "compact";

export function resolveViewportDensity(
  width: number,
  height: number,
): ViewportDensity {
  if (width < DESKTOP_BREAKPOINT) {
    return "comfortable";
  }

  return width <= COMPACT_DESKTOP_MAX_WIDTH &&
    height <= COMPACT_DESKTOP_MAX_HEIGHT
    ? "compact"
    : "comfortable";
}

/**
 * The density actually applied: the user's explicit choice when there is one,
 * otherwise the viewport heuristic above. lib/theme-script.ts repeats this
 * decision before hydration, and its test holds the two in agreement.
 */
export function resolveDensity(
  preference: DensityPreference,
  width: number,
  height: number,
): ViewportDensity {
  return preference === "auto"
    ? resolveViewportDensity(width, height)
    : preference;
}
