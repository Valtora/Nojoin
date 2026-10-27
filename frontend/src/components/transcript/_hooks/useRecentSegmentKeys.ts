"use client";

import { useCallback, useSyncExternalStore } from "react";
import { recentSegmentExpiry } from "../transcriptDisplay";
import { LINE_KEY_SEPARATOR, type IndexedTranscriptSegment } from "../transcriptGroups";

const serverSnapshot = () => "";

/** Recent visible line keys, with one timeout for the view's next badge expiry. */
export function useRecentSegmentKeys(items: readonly IndexedTranscriptSegment[]): string {
  const getSnapshot = useCallback(() => {
    const now = Date.now();
    return items
      .filter(({ segment }) => recentSegmentExpiry(segment, now) !== null)
      .map(({ segmentId }) => segmentId)
      .join(LINE_KEY_SEPARATOR);
  }, [items]);

  const subscribe = useCallback((onChange: () => void) => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const scheduleExpiry = () => {
      const now = Date.now();
      let nextExpiry = Infinity;
      for (const { segment } of items) {
        const expiry = recentSegmentExpiry(segment, now);
        if (expiry !== null) nextExpiry = Math.min(nextExpiry, expiry);
      }
      if (nextExpiry === Infinity) return;
      timer = setTimeout(() => {
        scheduleExpiry();
        onChange();
      }, nextExpiry - now);
    };
    scheduleExpiry();
    return () => {
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [items]);

  // A primitive snapshot stays equal between expiries, including under the
  // React Compiler; a clock read hidden in a render helper can be cached.
  return useSyncExternalStore(subscribe, getSnapshot, serverSnapshot);
}
