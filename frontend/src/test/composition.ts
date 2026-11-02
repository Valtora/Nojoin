import { fireEvent } from "@testing-library/react";

/**
 * Presses `key` on `element` the two ways a key reaches the page while an
 * input method is composing: flagged with `isComposing` (Chromium, Firefox),
 * and as keyCode 229 once Safari has ended the composition.
 */
export function pressWhileComposing(element: Element | Document, key: string) {
  fireEvent.keyDown(element, { key, isComposing: true });
  fireEvent.keyDown(element, { key, keyCode: 229 });
}
