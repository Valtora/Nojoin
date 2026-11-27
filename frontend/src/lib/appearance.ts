/**
 * Per-browser appearance preferences: colour palette, corners, density and
 * style. They sit beside the light/dark theme and follow the same rules: the
 * choice lives in local storage, the inline script in theme-script.ts stamps it
 * on <html> before first paint, and the React providers keep it in sync after
 * that. Every default is the product's original look, so an untouched browser
 * carries no attribute at all for palette and corners.
 *
 * The stylesheet side is attribute-driven: palettes.css keys its token blocks
 * on html[data-palette], tokens.css keys the radius scale on html[data-corners]
 * and the compact spacing on html[data-ui-density]. Style chrome keys on
 * html[data-style], which always carries an explicit value.
 */

import { applyUiStyleToRoot } from "./ui-style-loader";

export const THEMES = ["system", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

export const PALETTES = [
  "default",
  "graphite",
  "classic",
  "ultraviolet",
  "marigold",
] as const;
export type Palette = (typeof PALETTES)[number];

export const CORNER_STYLES = ["rounded", "subtle", "square"] as const;
export type CornerStyle = (typeof CORNER_STYLES)[number];

/** The Corner style setting: Automatic follows Style; the rest override it. */
export const CORNER_PREFERENCES = ["auto", ...CORNER_STYLES] as const;
export type CornerPreference = (typeof CORNER_PREFERENCES)[number];

export const UI_STYLES = ["default", "classic", "depth"] as const;
export type UiStyle = (typeof UI_STYLES)[number];

/** The corners each style brings while Corner style is Automatic. */
export const STYLE_CORNERS: Record<UiStyle, CornerStyle> = {
  default: "rounded",
  classic: "square",
  depth: "rounded",
};

export const DENSITY_PREFERENCES = ["auto", "comfortable", "compact", "dense"] as const;
export type DensityPreference = (typeof DENSITY_PREFERENCES)[number];

export const DEFAULT_THEME: Theme = "system";
export const DEFAULT_PALETTE: Palette = "default";
/** The corners with no attribute on <html>: the original look. */
export const DEFAULT_CORNER_STYLE: CornerStyle = "rounded";
export const DEFAULT_CORNER_PREFERENCE: CornerPreference = "auto";
export const DEFAULT_UI_STYLE: UiStyle = "default";
export const DEFAULT_DENSITY_PREFERENCE: DensityPreference = "auto";

export const APPEARANCE_STORAGE_KEYS = {
  theme: "nojoin-theme",
  palette: "nojoin-palette",
  corners: "nojoin-corners",
  style: "nojoin-style",
  density: "nojoin-density",
} as const;

export const PALETTE_LABELS: Record<Palette, string> = {
  default: "Nojoin (orange)",
  graphite: "Graphite (blue)",
  classic: "Classic (grey and navy)",
  ultraviolet: "Ultraviolet (magenta)",
  marigold: "Marigold (forest and gold)",
};

export const CORNER_PREFERENCE_LABELS: Record<CornerPreference, string> = {
  auto: "Automatic (matches Style)",
  rounded: "Rounded",
  subtle: "Subtle",
  square: "Square",
};

export const UI_STYLE_LABELS: Record<UiStyle, string> = {
  default: "Default",
  classic: "Classic",
  depth: "Depth",
};

export const DENSITY_PREFERENCE_LABELS: Record<DensityPreference, string> = {
  auto: "Automatic (by window size)",
  comfortable: "Comfortable",
  compact: "Compact",
  dense: "Dense",
};

function readStored<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): T {
  if (typeof window === "undefined") {
    return fallback;
  }

  try {
    const stored = window.localStorage.getItem(key);
    return allowed.includes(stored as T) ? (stored as T) : fallback;
  } catch {
    // Storage can be unavailable (privacy modes, blocked site data); the
    // default is always a valid answer.
    return fallback;
  }
}

function writeStored(key: string, value: string, isDefault: boolean) {
  try {
    if (isDefault) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, value);
    }
  } catch {
    // The choice still applies for this page view; it just will not persist.
  }
}

export function readStoredTheme(): Theme {
  return readStored(APPEARANCE_STORAGE_KEYS.theme, THEMES, DEFAULT_THEME);
}

export function storeTheme(theme: Theme) {
  writeStored(APPEARANCE_STORAGE_KEYS.theme, theme, theme === DEFAULT_THEME);
}

export function readStoredPalette(): Palette {
  return readStored(APPEARANCE_STORAGE_KEYS.palette, PALETTES, DEFAULT_PALETTE);
}

/**
 * The stored Corner style. Before Automatic existed, Rounded was stored as no
 * key at all, so a browser left on Rounded now follows Style; an explicit
 * Subtle or Square is kept as it was.
 */
export function readStoredCornerPreference(): CornerPreference {
  return readStored(APPEARANCE_STORAGE_KEYS.corners, CORNER_PREFERENCES, DEFAULT_CORNER_PREFERENCE);
}

/** The corners that show: the explicit choice, or under Automatic the style's own. */
export function resolveCornerStyle(preference: CornerPreference, style: UiStyle): CornerStyle {
  return preference === "auto" ? STYLE_CORNERS[style] : preference;
}

export function readStoredUiStyle(): UiStyle {
  return readStored(APPEARANCE_STORAGE_KEYS.style, UI_STYLES, DEFAULT_UI_STYLE);
}

export function readStoredDensityPreference(): DensityPreference {
  return readStored(
    APPEARANCE_STORAGE_KEYS.density,
    DENSITY_PREFERENCES,
    DEFAULT_DENSITY_PREFERENCE,
  );
}

export function storePalette(palette: Palette) {
  writeStored(APPEARANCE_STORAGE_KEYS.palette, palette, palette === DEFAULT_PALETTE);
}

export function storeCornerPreference(corners: CornerPreference) {
  writeStored(APPEARANCE_STORAGE_KEYS.corners, corners, corners === DEFAULT_CORNER_PREFERENCE);
}

export function storeUiStyle(style: UiStyle) {
  writeStored(APPEARANCE_STORAGE_KEYS.style, style, style === DEFAULT_UI_STYLE);
}

/** Apply the style and load optional chrome only while it is selected. */
export function applyUiStyle(style: UiStyle, root: HTMLElement = document.documentElement) {
  applyUiStyleToRoot(style, root);
}

export function storeDensityPreference(preference: DensityPreference) {
  writeStored(
    APPEARANCE_STORAGE_KEYS.density,
    preference,
    preference === DEFAULT_DENSITY_PREFERENCE,
  );
}

/** Stamp the palette on <html>; the default palette is the absence of the attribute. */
export function applyPalette(palette: Palette, root: HTMLElement = document.documentElement) {
  if (palette === DEFAULT_PALETTE) {
    delete root.dataset.palette;
  } else {
    root.dataset.palette = palette;
  }
}

/** Stamp the corner style on <html>; rounded is the absence of the attribute. */
export function applyCornerStyle(
  corners: CornerStyle,
  root: HTMLElement = document.documentElement,
) {
  if (corners === DEFAULT_CORNER_STYLE) {
    delete root.dataset.corners;
  } else {
    root.dataset.corners = corners;
  }
}
