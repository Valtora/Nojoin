import { useState } from "react";

/**
 * `value`, or the one it replaced when the two hold the same contents. The
 * recording page's poll hands the view new speaker lists and maps every few
 * seconds even when nothing changed; keeping the old object keeps the
 * memoised lines that read it from redrawing.
 *
 * The contents are compared as JSON, and only when a new object arrives, so
 * a value must be plain data: no functions, Maps or Sets.
 */
export function useContentStable<T>(value: T): T {
  const [state, setState] = useState({ seen: value, kept: value });
  if (state.seen === value) return state.kept;
  const kept = JSON.stringify(value) === JSON.stringify(state.kept) ? state.kept : value;
  // Stored during render, as React's docs do for a value derived from the
  // previous render; this render already returns the result.
  setState({ seen: value, kept });
  return kept;
}
