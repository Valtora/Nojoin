"use client";

import { useMemo, useSyncExternalStore } from "react";
import { recentSegmentExpiry } from "../transcriptDisplay";
import { LINE_KEY_SEPARATOR, type IndexedTranscriptSegment } from "../transcriptGroups";

/** After a badge expires, the next expiry waits at least this long, so a burst clears together. */
const EXPIRY_BATCH_MS = 1_000;

const serverSnapshot = () => "";

/**
 * The recent keys of `items` as of the store's own clock, which reads the time
 * once and then only when its timer fires. A snapshot that read the time itself
 * changed between React's reads whenever a badge expired mid-render, and React
 * re-rendered until it gave up: a long transcript written in one burst crashed
 * the page 15 s later (issue #831).
 */
function recentSegmentKeysStore(items: readonly IndexedTranscriptSegment[]) {
  let clock: number | null = null;
  let keys = "";
  let lastFired = -Infinity;
  const readClock = (now: number) => {
    clock = now;
    keys = items
      .filter(({ segment }) => recentSegmentExpiry(segment, now) !== null)
      .map(({ segmentId }) => segmentId)
      .join(LINE_KEY_SEPARATOR);
    return now;
  };
  const getSnapshot = () => {
    if (clock === null) readClock(Date.now());
    return keys;
  };
  const subscribe = (onChange: () => void) => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const scheduleExpiry = () => {
      const now = clock ?? readClock(Date.now());
      let nextExpiry = Infinity;
      for (const { segment } of items) {
        const expiry = recentSegmentExpiry(segment, now);
        if (expiry !== null) nextExpiry = Math.min(nextExpiry, expiry);
      }
      if (nextExpiry === Infinity) return;
      const at = Math.max(nextExpiry, lastFired + EXPIRY_BATCH_MS);
      timer = setTimeout(() => {
        lastFired = readClock(Date.now());
        scheduleExpiry();
        onChange();
      }, Math.max(0, at - Date.now()));
    };
    scheduleExpiry();
    return () => {
      if (timer !== undefined) clearTimeout(timer);
    };
  };
  return { getSnapshot, subscribe };
}

/** Recent visible line keys, with one timeout for the view's next badge expiry. */
export function useRecentSegmentKeys(items: readonly IndexedTranscriptSegment[]): string {
  // A primitive snapshot stays equal between expiries, including under the
  // React Compiler; a clock read hidden in a render helper can be cached.
  const store = useMemo(() => recentSegmentKeysStore(items), [items]);
  return useSyncExternalStore(store.subscribe, store.getSnapshot, serverSnapshot);
}
