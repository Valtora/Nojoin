/**
 * Reads an element's Tailwind transition utilities, since jsdom loads no
 * stylesheet and so has no computed `transition` to inspect.
 */

/** The transition utilities that animate something, `transition-none` aside. */
export function activeTransitions(element: Element): string[] {
  return [...element.classList].filter(
    (name) => /^transition(-|$)/.test(name) && name !== "transition-none",
  );
}

/** Whether a change to the element's inline width animates. */
export function animatesWidth(element: Element): boolean {
  const transitionsWidth = activeTransitions(element).some(
    (name) => name === "transition-all" || /^transition-\[[^\]]*\bwidth\b/.test(name),
  );
  const hasDuration = [...element.classList].some((name) => /^duration-\d+$/.test(name));
  return transitionsWidth && hasDuration;
}
