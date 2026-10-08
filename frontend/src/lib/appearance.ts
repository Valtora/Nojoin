/**
 * Per-browser appearance preferences: colour palette, corner style and
 * density. They sit beside the light/dark theme and follow the same rules: the
 * choice lives in local storage, the inline script in theme-script.ts stamps it
 * on <html> before first paint, and the React providers keep it in sync after
 * that. Every default reproduces the product exactly as it looked before these
 * settings existed, so an untouched browser carries no attribute at all for
 * palette and corners.
 *
 * The stylesheet side is attribute-driven: palettes.css keys its token blocks
 * on html[data-palette], tokens.css keys the radius scale on html[data-corners]
 * and the compact spacing on html[data-ui-density].
 */

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

export const DENSITY_PREFERENCES = ["auto", "comfortable", "compact", "dense"] as const;
export type DensityPreference = (typeof DENSITY_PREFERENCES)[number];

export const DEFAULT_THEME: Theme = "system";
export const DEFAULT_PALETTE: Palette = "default";
export const DEFAULT_CORNER_STYLE: CornerStyle = "rounded";
export const DEFAULT_DENSITY_PREFERENCE: DensityPreference = "auto";

export const APPEARANCE_STORAGE_KEYS = {
  theme: "nojoin-theme",
  palette: "nojoin-palette",
  corners: "nojoin-corners",
  density: "nojoin-density",
} as const;

export const PALETTE_LABELS: Record<Palette, string> = {
  default: "Nojoin (orange)",
  graphite: "Graphite (blue)",
  classic: "Classic",
  ultraviolet: "Ultraviolet (magenta)",
  marigold: "Marigold (forest and gold)",
};

export const CORNER_STYLE_LABELS: Record<CornerStyle, string> = {
  rounded: "Rounded",
  subtle: "Subtle",
  square: "Square",
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

export function readStoredCornerStyle(): CornerStyle {
  return readStored(APPEARANCE_STORAGE_KEYS.corners, CORNER_STYLES, DEFAULT_CORNER_STYLE);
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

export function storeCornerStyle(corners: CornerStyle) {
  writeStored(APPEARANCE_STORAGE_KEYS.corners, corners, corners === DEFAULT_CORNER_STYLE);
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
