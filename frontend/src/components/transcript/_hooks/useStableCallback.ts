"use client";

import { useCallback, useLayoutEffect, useRef } from "react";

/**
 * A function that keeps its identity for the component's life and calls the
 * latest `callback` it was given.
 *
 * For handlers a memoised child only calls from an event: the recording page
 * builds new ones on every playback tick, and passing those through would
 * redraw every line. Not for anything called while rendering or committing,
 * which would see the previous render's callback.
 */
export function useStableCallback<Args extends unknown[], Result>(
  callback: (...args: Args) => Result,
): (...args: Args) => Result {
  const latest = useRef(callback);
  useLayoutEffect(() => {
    latest.current = callback;
  });
  return useCallback((...args: Args) => latest.current(...args), []);
}
