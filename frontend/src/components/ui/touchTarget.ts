/**
 * On a touch screen a control takes at least the full 44 × 44 px target. The
 * sizes are px, not rem, so it holds in every density: compact and dense set
 * a 15 px root, which would take a rem-sized `Button` at `sm` or an
 * `IconButton` under it. The minimum width covers a short label ("All").
 */
export const TOUCH_TARGET_HEIGHT = "pointer-coarse:h-[44px] pointer-coarse:min-w-[44px]";

/** The same for a square `IconButton`. */
export const TOUCH_TARGET_SQUARE = "pointer-coarse:h-[44px] pointer-coarse:w-[44px]";
