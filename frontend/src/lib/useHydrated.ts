import { useSyncExternalStore } from "react";

const subscribe = () => () => {};
const clientSnapshot = () => true;
const serverSnapshot = () => false;

/**
 * False while the server-rendered HTML is being hydrated, true once the client
 * has taken over.
 *
 * useSyncExternalStore gives React the server snapshot during hydration and
 * the client one straight after, which is what a `mounted` flag set from an
 * effect did by hand, with an extra render pass and a setState-in-effect lint
 * violation. Use it to gate anything the server cannot render the same way:
 * portals, browser-only APIs, values read from storage.
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(subscribe, clientSnapshot, serverSnapshot);
}
