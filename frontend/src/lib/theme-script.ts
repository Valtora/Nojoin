// Inline script that applies the stored appearance before React hydrates, so
// the first paint is already in the right theme, palette, corner style and
// density instead of flashing the defaults.
//
// The string is shipped to the browser verbatim and never compiled, so it must
// stay plain JavaScript: a TypeScript annotation in here is a syntax error that
// silently disables the whole script. The constants are interpolated from the
// modules the React side uses so the two cannot drift, and
// theme-script.test.ts executes the string itself.

import {
  APPEARANCE_STORAGE_KEYS,
  CORNER_STYLES,
  DEFAULT_CORNER_STYLE,
  DEFAULT_PALETTE,
  DENSITY_PREFERENCES,
  PALETTES,
} from "./appearance";
import {
  COMPACT_DESKTOP_MAX_HEIGHT,
  COMPACT_DESKTOP_MAX_WIDTH,
  DESKTOP_BREAKPOINT,
} from "./viewportDensity";

const THEMES = ["light", "dark", "system"];

export const themeScript = `
(function () {
  var root = document.documentElement;
  var keys = ${JSON.stringify(APPEARANCE_STORAGE_KEYS)};

  function read(key, allowed) {
    try {
      var value = window.localStorage.getItem(key);
      return allowed.indexOf(value) === -1 ? null : value;
    } catch (error) {
      return null;
    }
  }

  var prefersDark = false;
  try {
    prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  } catch (error) {
    prefersDark = false;
  }

  var theme = read(keys.theme, ${JSON.stringify(THEMES)}) || 'system';
  if (theme === 'dark' || (theme === 'system' && prefersDark)) {
    root.classList.add('dark');
  } else {
    root.classList.remove('dark');
  }

  var palette = read(keys.palette, ${JSON.stringify(PALETTES)});
  if (palette && palette !== ${JSON.stringify(DEFAULT_PALETTE)}) {
    root.setAttribute('data-palette', palette);
  }

  var corners = read(keys.corners, ${JSON.stringify(CORNER_STYLES)});
  if (corners && corners !== ${JSON.stringify(DEFAULT_CORNER_STYLE)}) {
    root.setAttribute('data-corners', corners);
  }

  var density = read(keys.density, ${JSON.stringify(DENSITY_PREFERENCES)}) || 'auto';
  if (density === 'auto') {
    var width = window.innerWidth;
    var height = window.innerHeight;
    density = width >= ${DESKTOP_BREAKPOINT} &&
      width <= ${COMPACT_DESKTOP_MAX_WIDTH} &&
      height <= ${COMPACT_DESKTOP_MAX_HEIGHT}
      ? 'compact'
      : 'comfortable';
  }
  root.setAttribute('data-ui-density', density);
})();
`;
