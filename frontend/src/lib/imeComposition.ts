type KeyPress = Pick<KeyboardEvent, "isComposing" | "keyCode">;

/**
 * Whether a key press belongs to an input method's composition (Chinese,
 * Japanese, Korean and other IMEs), where Enter, Escape, Tab and the arrow
 * keys choose or cancel a candidate and must not act on the page as well.
 *
 * Chromium and Firefox flag the press with `isComposing`. Safari ends the
 * composition first and reports the key that ended it as keyCode 229, with
 * `isComposing` already false.
 *
 * Takes a DOM event or a React one, whose `isComposing` is on `nativeEvent`.
 */
export function isImeComposing(event: KeyPress | { nativeEvent: KeyPress }): boolean {
  const press = "nativeEvent" in event ? event.nativeEvent : event;
  return press.isComposing || press.keyCode === 229;
}
