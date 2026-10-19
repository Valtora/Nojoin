/**
 * The control vocabulary every settings page draws from.
 *
 * These constants are the single definition of a settings control: radius,
 * border, background, focus ring and disabled treatment, matched to the design
 * system's 8px control radius (Tailwind's rounded-lg).
 *
 * Import these rather than writing control classes by hand, so a change to the
 * focus ring or the disabled state lands everywhere at once.
 *
 * The recipes deliberately mirror the `Input` and `Button` primitives token for
 * token, so a settings field and a field anywhere else cannot drift apart. They
 * exist as strings rather than as components only because settings pages apply
 * them to elements they render themselves, including native selects and
 * textareas the primitives do not wrap.
 */

const CONTROL_BASE =
  "w-full rounded-lg border border-control-border bg-control-bg px-3 py-2 text-sm text-foreground transition-colors duration-150 placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring disabled:cursor-not-allowed disabled:border-control-disabled-border disabled:bg-control-disabled-bg disabled:text-control-disabled-fg";

export const SETTINGS_INPUT_CLASS = CONTROL_BASE;

export const SETTINGS_SELECT_CLASS = CONTROL_BASE;

export const SETTINGS_TEXTAREA_CLASS = `${CONTROL_BASE} min-h-28 resize-y leading-6`;

const BUTTON_BASE =
  "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold whitespace-nowrap transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring disabled:cursor-not-allowed disabled:opacity-60";

export const SETTINGS_BUTTON_PRIMARY = `${BUTTON_BASE} border border-transparent bg-action text-action-on hover:bg-action-hover active:bg-action-active`;

export const SETTINGS_BUTTON_SECONDARY = `${BUTTON_BASE} border border-control-border bg-surface-card font-medium text-foreground hover:bg-surface-inset active:bg-surface-inset`;

export const SETTINGS_BUTTON_DANGER = `${BUTTON_BASE} border border-danger-text bg-surface-card font-medium text-danger-text hover:bg-surface-inset active:bg-surface-inset`;

/** A read-only value in a row's control column, such as one set in `.env`. */
export const SETTINGS_VALUE_CLASS =
  "text-sm text-foreground break-words @min-[26rem]:text-right";

/**
 * A row's control column at a fixed width, for cards whose selects would
 * otherwise size to their longest option and leave the columns ragged.
 */
export const SETTINGS_CONTROL_COLUMN_WIDE = "@min-[26rem]:w-80 @min-[26rem]:max-w-none";

/**
 * A row's control column that holds a switch or a lone button against the
 * card's right edge, where the default column would leave it on the left.
 */
export const SETTINGS_CONTROL_END = "@min-[26rem]:min-w-0 @min-[26rem]:flex @min-[26rem]:justify-end";
