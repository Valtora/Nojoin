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
