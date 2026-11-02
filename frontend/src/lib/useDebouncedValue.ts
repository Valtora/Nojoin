import { useEffect, useState } from "react";

/** How long a search box waits after the last keystroke before it searches. */
export const SEARCH_DEBOUNCE_MS = 300;

/**
 * `value` once it has stayed the same for `delay` ms. Each change restarts
 * the wait, so typing a word sends one search rather than one per letter.
 */
export function useDebouncedValue<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);

  return settled;
}
