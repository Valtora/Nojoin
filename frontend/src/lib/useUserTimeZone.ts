import { useEffect, useSyncExternalStore } from "react";

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
 * re-renders in the new zone.
 */
export function useUserTimeZone(): string {
  const timeZone = useSyncExternalStore(
    subscribeUserTimeZone,
    getCachedUserTimeZone,
    serverSnapshot,
  );

  useEffect(() => {
    if (getCachedUserTimeZone() === null) {
      void getUserTimeZone();
    }
  }, []);

  return timeZone ?? DEFAULT_TIME_ZONE;
}
