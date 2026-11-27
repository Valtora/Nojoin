import { useSyncExternalStore } from "react";

const subscribe = (onChange: () => void) => {
  window.addEventListener("popstate", onChange);
  return () => window.removeEventListener("popstate", onChange);
};

/**
 * One query-string parameter of the current URL, or null.
 *
 * Read through useSyncExternalStore so the server render and the first client
 * render agree (both see null), and the value appears right after hydration
 * without setting state from an effect. Unlike next/navigation's
 * useSearchParams it needs no Suspense boundary around the page.
 */
export function useLocationSearchParam(name: string): string | null {
  return useSyncExternalStore(
    subscribe,
    () => new URLSearchParams(window.location.search).get(name),
    () => null,
  );
}
