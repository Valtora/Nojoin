import { useEffect, useState, useSyncExternalStore } from "react";

import {
  DEFAULT_TIME_ZONE,
  getCachedUserTimeZone,
  getUserTimeZone,
  subscribeUserTimeZone,
} from "@/lib/timezone";

const serverSnapshot = (): string | null => null;

/**
 * The user's Nojoin time-zone setting, for showing a date or time.
 *
 * UTC until the setting has loaded, as on the server, so hydration matches.
 * The settings page updates the cached zone when it saves, and a mounted view
 * re-renders in the new zone. If the setting cannot be loaded, the browser's
 * zone stands in for this view only, and the next view to mount asks again.
 */
export function useUserTimeZone(): string {
  const cached = useSyncExternalStore(
    subscribeUserTimeZone,
    getCachedUserTimeZone,
    serverSnapshot,
  );
  const [fallback, setFallback] = useState<string | null>(null);

  useEffect(() => {
    if (getCachedUserTimeZone() !== null) {
      return;
    }
    let active = true;
    void getUserTimeZone().then((timeZone) => {
      if (active) {
        setFallback(timeZone);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  return cached ?? fallback ?? DEFAULT_TIME_ZONE;
}
