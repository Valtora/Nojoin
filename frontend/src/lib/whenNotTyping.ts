// Input types that are a button, a toggle or a picker rather than a box to type in.
const NON_TEXT_INPUT_TYPES = new Set([
  "button", "checkbox", "color", "file", "hidden", "image", "radio", "range", "reset", "submit",
]);

/**
 * Whether an element takes typed text: an input that is a text box of any
 * kind, a textarea, or rich text such as a Tiptap editor, whose ProseMirror
 * root is contenteditable.
 */
export function isTextEntry(element: Element | null): boolean {
  if (element instanceof HTMLInputElement) return !NON_TEXT_INPUT_TYPES.has(element.type) && !element.readOnly;
  if (element instanceof HTMLTextAreaElement) return !element.readOnly;
  if (!(element instanceof HTMLElement)) return false;
  if (typeof element.isContentEditable === "boolean") return element.isContentEditable;
  // jsdom has no isContentEditable: the nearest contenteditable attribute decides.
  const host = element.closest("[contenteditable]");
  return host !== null && host.getAttribute("contenteditable") !== "false";
}

function focusedElement(): Element | null {
  let focused = document.activeElement;
  while (focused?.shadowRoot?.activeElement) focused = focused.shadowRoot.activeElement;
  return focused;
}

// The events that say someone is still at the keyboard, or about to be.
const ACTIVITY_EVENTS = ["focusin", "focusout", "keydown", "pointerdown", "compositionstart", "compositionend"];

/**
 * Runs `run` after `delayMs`, unless someone is typing then: a text field has
 * focus, or an input method's composition is open (Chinese, Japanese, Korean).
 * It then waits until neither holds and the page has been quiet for `idleMs`:
 * every focus change, key press or click starts that wait again, and no timer
 * runs between them. A key press arms it too, since a field removed by its own
 * Enter or Escape may take its focus without a focusout.
 *
 * Returns a cancel that removes the timer and the listeners. Both are also
 * gone once `run` has run.
 */
export function whenNotTyping(run: () => void, delayMs: number, idleMs: number): () => void {
  let composingIn: Node | null = null;
  let waiting = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  // A composition belongs to the element that had focus when it began, and
  // ends when that element loses focus, even if no compositionend arrives.
  const composing = () =>
    composingIn !== null && composingIn.isConnected && composingIn.contains(document.activeElement);
  const stop = () => {
    clearTimeout(timer);
    timer = undefined;
    for (const type of ACTIVITY_EVENTS) document.removeEventListener(type, onActivity, true);
  };
  const attempt = () => {
    timer = undefined;
    if (composing() || isTextEntry(focusedElement())) {
      waiting = true;
      return;
    }
    stop();
    run();
  };
  function onActivity(event: Event) {
    if (event.type === "compositionstart") composingIn = event.target instanceof Node ? event.target : null;
    else if (event.type === "compositionend") composingIn = null;
    if (!waiting) return;
    clearTimeout(timer);
    timer = setTimeout(attempt, idleMs);
  }

  for (const type of ACTIVITY_EVENTS) document.addEventListener(type, onActivity, true);
  timer = setTimeout(attempt, delayMs);
  return stop;
}
