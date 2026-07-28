/**
 * The control vocabulary every settings page draws from.
 *
 * Settings previously used seven different border radii and a different input
 * class per component, because each one styled its own fields inline. These
 * constants are the single definition: radius, border, background, focus ring
 * and disabled treatment, matched to the design system's 8px control radius
 * (Tailwind's rounded-lg).
 *
 * Import these rather than writing control classes by hand, so a change to the
 * focus ring or the disabled state lands everywhere at once.
 */

const CONTROL_BASE =
  "w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 transition-colors placeholder:text-gray-500 focus:border-transparent focus:ring-2 focus:ring-orange-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:text-white dark:placeholder:text-gray-400";

export const SETTINGS_INPUT_CLASS = CONTROL_BASE;

export const SETTINGS_SELECT_CLASS = CONTROL_BASE;

export const SETTINGS_TEXTAREA_CLASS = `${CONTROL_BASE} min-h-28 resize-y leading-6`;

export const SETTINGS_BUTTON_PRIMARY =
  "inline-flex items-center justify-center gap-2 rounded-lg bg-orange-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-orange-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-orange-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-orange-300 dark:focus-visible:ring-offset-gray-950 dark:disabled:bg-orange-900/40";

export const SETTINGS_BUTTON_SECONDARY =
  "inline-flex items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-800 transition-colors hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-orange-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 dark:hover:bg-gray-800";

export const SETTINGS_BUTTON_DANGER =
  "inline-flex items-center justify-center gap-2 rounded-lg border border-red-300 bg-white px-4 py-2 text-sm font-medium text-red-700 transition-colors hover:bg-red-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-500/40 dark:bg-gray-900 dark:text-red-300 dark:hover:bg-red-500/10";
