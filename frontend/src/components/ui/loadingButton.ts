import type { MouseEvent, MouseEventHandler } from "react";

/**
 * The disabled look, for a native `disabled` and for a loading button alike.
 */
export const DISABLED_LOOK =
  "disabled:cursor-not-allowed disabled:opacity-60 aria-disabled:cursor-not-allowed aria-disabled:opacity-60";

const ignoreClick = (event: MouseEvent<HTMLButtonElement>) => event.preventDefault();

/**
 * The attributes `Button` and `IconButton` put on a loading button. A raw
 * `<button>` that runs its own request spreads them too, with `DISABLED_LOOK`
 * or its own `aria-disabled:` classes for the look (#614).
 *
 * A loading button stays enabled, so it keeps keyboard focus: Chrome moves
 * focus from a button to the page body once it is disabled, which in a dialog
 * also leaves the focus trap (#539). It reads as disabled and busy instead,
 * and a click does nothing. Cancelling the click also stops a submit button
 * sending its form, including the click a browser sends for Enter in a field,
 * so a form cannot be sent twice. Loading outranks `disabled`, because callers
 * often disable the button for the same wait.
 */
export function loadingButtonProps(
  loading: boolean,
  disabled?: boolean,
  onClick?: MouseEventHandler<HTMLButtonElement>,
) {
  if (!loading) return { disabled, onClick };
  return {
    disabled: false,
    "aria-disabled": true,
    "aria-busy": true,
    onClick: ignoreClick,
  } as const;
}
