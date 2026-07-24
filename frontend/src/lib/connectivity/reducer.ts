/**
 * Pure state machine for backend connectivity.
 *
 * The previous design equated "a synthetic health probe failed" with "the
 * backend is down". That is a category error: a throttled background tab, a
 * saturated connection pool, a sleeping laptop or a dropped link all fail
 * probes while saying nothing about the server. This machine instead treats
 * every observed success — a real API response, a health-content poll, or an
 * idle liveness probe — as authoritative proof of reachability, and only
 * escalates to `unreachable` once the browser is online, no success has been
 * seen for a while, AND a dedicated probe has failed repeatedly.
 *
 * Reachability is a derived function of the inputs, so the reducer is a pure
 * function of (state, event) and is exhaustively unit-testable without mocking
 * timers or the network. All timing lives in the driver (monitor.ts).
 */

export type Reachability = "online" | "checking" | "offline" | "unreachable";

export interface ConnectivityState {
  status: Reachability;
  /** Timestamp (ms) of the last proof the backend answered. */
  lastReachableAt: number | null;
  /** Consecutive dedicated-probe failures; only the prober confirms outages. */
  probeFailureStreak: number;
  /** navigator.onLine, tracked via the browser's online/offline events. */
  browserOnline: boolean;
  /** document.visibilityState === "visible"; hidden tabs are not probed. */
  visible: boolean;
}

export type ConnectivityEvent =
  | { type: "request-succeeded"; at: number }
  | { type: "request-failed"; at: number; gateway: boolean }
  | { type: "probe-succeeded"; at: number }
  | { type: "probe-failed"; at: number }
  | { type: "browser-online"; at: number }
  | { type: "browser-offline"; at: number }
  | { type: "visibility-changed"; at: number; visible: boolean }
  | { type: "evaluate"; at: number };

/**
 * A success (real traffic or probe) keeps the machine `online` for this long
 * before staleness forces a re-check. Longer than any single poll interval so
 * one missed beat never destales a healthy backend.
 */
export const STALE_AFTER_MS = 30_000;

/** Dedicated-probe failures required, once stale, to declare `unreachable`. */
export const PROBE_FAILURES_TO_UNREACHABLE = 3;

export const createInitialState = (): ConnectivityState => ({
  // Optimistic: assume reachable until the first probe establishes ground
  // truth, so a fresh load never flashes a false alarm.
  status: "online",
  lastReachableAt: null,
  probeFailureStreak: 0,
  browserOnline: true,
  visible: true,
});

const deriveStatus = (
  state: Omit<ConnectivityState, "status">,
  now: number,
): Reachability => {
  if (!state.browserOnline) {
    return "offline";
  }

  const reachableRecently =
    state.lastReachableAt !== null &&
    now - state.lastReachableAt < STALE_AFTER_MS;

  // Before any probe has run and with no failures observed, stay optimistic.
  const optimisticStartup =
    state.lastReachableAt === null && state.probeFailureStreak === 0;

  if (reachableRecently || optimisticStartup) {
    return "online";
  }

  return state.probeFailureStreak >= PROBE_FAILURES_TO_UNREACHABLE
    ? "unreachable"
    : "checking";
};

const withStatus = (
  state: Omit<ConnectivityState, "status">,
  now: number,
): ConnectivityState => ({ ...state, status: deriveStatus(state, now) });

export const reduce = (
  state: ConnectivityState,
  event: ConnectivityEvent,
): ConnectivityState => {
  switch (event.type) {
    case "request-succeeded":
    case "probe-succeeded":
      // Any answer from the server is proof of life; clear the failure streak
      // and refresh the reachability clock. A success also means we are online
      // regardless of a stale navigator.onLine flag.
      return withStatus(
        {
          ...state,
          browserOnline: true,
          lastReachableAt: event.at,
          probeFailureStreak: 0,
        },
        event.at,
      );

    case "probe-failed":
      // Only the dedicated prober increments the confirmation streak.
      return withStatus(
        { ...state, probeFailureStreak: state.probeFailureStreak + 1 },
        event.at,
      );

    case "request-failed":
      // A real request without a server response is ambiguous (timeout, tab
      // throttle, one blocked socket). It never alarms on its own — it only
      // lets time advance so the driver can schedule a confirming probe.
      return withStatus(state, event.at);

    case "browser-offline":
      return { ...state, browserOnline: false, status: "offline" };

    case "browser-online":
      return withStatus({ ...state, browserOnline: true }, event.at);

    case "visibility-changed":
      return withStatus({ ...state, visible: event.visible }, event.at);

    case "evaluate":
      return withStatus(state, event.at);

    default: {
      const _exhaustive: never = event;
      return _exhaustive;
    }
  }
};

/** True when the backend should be treated as usable by the UI. */
export const isReachable = (status: Reachability): boolean =>
  status === "online" || status === "checking";
