import { create } from "zustand";

import {
  createInitialState,
  reduce,
  STALE_AFTER_MS,
  type ConnectivityEvent,
  type ConnectivityState,
} from "./reducer";

/** Cadence when we have fresh proof of life: probe lazily, only to fill idle gaps. */
const FRESH_PROBE_INTERVAL_MS = 15_000;
/** Cadence while suspected-down: confirm the outage (or detect recovery) quickly. */
const CONFIRM_PROBE_INTERVAL_MS = 5_000;
/** Brief delay used to bring a probe forward after a real request fails. */
const REACTIVE_PROBE_DELAY_MS = 500;
/** Liveness-probe timeout. Deliberately generous — a slow answer is not an outage. */
const PROBE_TIMEOUT_MS = 10_000;
/**
 * A failed probe is only evidence if it was raced against the network for
 * roughly its own timeout. Take this much longer and the browser was not
 * running our code: a frozen or suspended tab stops the abort timer with the
 * request, and both resume together, so the fetch is cancelled the instant the
 * tab thaws without ever having been given a fair 10 seconds. Counting that as
 * a confirmed failure turns a browser suspension into a backend outage.
 *
 * The `resume` event is not usable for this. It is advisory and not dispatched
 * on every Chromium build (issue #166), so the elapsed wall clock is the only
 * signal that holds everywhere.
 */
const SUSPENDED_PROBE_AFTER_MS = PROBE_TIMEOUT_MS * 2;

/**
 * Cheap, unauthenticated, redirect-free liveness endpoint. Mirrors the axios
 * client's base-URL resolution but targets `/health` (outside `/v1`).
 */
const LIVENESS_URL = `${(process.env.NEXT_PUBLIC_API_URL || "/api").replace(/\/$/, "")}/health`;

type TimerHandle = number;

interface ConnectivityStore extends ConnectivityState {
  dispatch: (event: ConnectivityEvent) => void;
}

export const useConnectivityStore = create<ConnectivityStore>((set) => ({
  ...createInitialState(),
  dispatch: (event) => set((state) => reduce(state, event)),
}));

export interface RequestOutcome {
  /** True when the server produced any HTTP response (even 4xx/5xx). */
  reachedServer: boolean;
  /** True for 502/503/504 — proxy up, backend not answering. */
  gateway?: boolean;
}

export interface MonitorScheduler {
  now: () => number;
  setTimer: (fn: () => void, ms: number) => TimerHandle;
  clearTimer: (handle: TimerHandle) => void;
}

export interface MonitorDeps extends MonitorScheduler {
  dispatch: (event: ConnectivityEvent) => void;
  getState: () => ConnectivityState;
  /** Runs one liveness probe; resolves true when the backend answered ok. */
  probe: () => Promise<boolean>;
}

/**
 * Drives the pure reducer: schedules idle-fallback probes, reacts to real
 * request outcomes and browser connectivity/visibility events. All timing and
 * I/O is injected so the scheduling logic is deterministically testable.
 */
export class ConnectivityMonitor {
  private timer: TimerHandle | null = null;

  private running = false;

  /**
   * One probe at a time, always. Without this the driver stacks them: a tick
   * that is awaiting its probe holds no timer, so every failing request calls
   * `bringProbeForward` and schedules another tick 500ms later, which starts
   * another probe. A tab that resumes from suspension fails many requests at
   * once and produced seven concurrent probes in production, all aborted in the
   * same instant, which is three times the confirmation threshold delivered in
   * one go. The threshold means "three probes over ~15 seconds", and only a
   * serialised prober makes that true.
   */
  private probing = false;

  private readonly listeners: Array<() => void> = [];

  constructor(private readonly deps: MonitorDeps) {}

  start(): void {
    if (this.running) {
      return;
    }
    this.running = true;
    this.syncEnvironment();
    this.attachBrowserListeners();
    // Defer the first tick through the scheduler so the initial probe is
    // observable/controllable and start() never leaves a floating promise.
    this.timer = this.deps.setTimer(() => void this.tick(), 0);
  }

  stop(): void {
    this.running = false;
    this.clearTimer();
    this.listeners.splice(0).forEach((detach) => detach());
  }

  recordRequestOutcome(outcome: RequestOutcome): void {
    const at = this.deps.now();
    if (outcome.reachedServer) {
      this.deps.dispatch({ type: "request-succeeded", at });
      return;
    }

    this.deps.dispatch({
      type: "request-failed",
      at,
      gateway: Boolean(outcome.gateway),
    });
    // A real failure is our cue to verify quickly rather than wait for the
    // next idle tick — but only the dedicated probe can confirm an outage.
    this.bringProbeForward();
  }

  private syncEnvironment(): void {
    const at = this.deps.now();
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      this.deps.dispatch({ type: "browser-offline", at });
    }
    if (typeof document !== "undefined") {
      this.deps.dispatch({
        type: "visibility-changed",
        at,
        visible: document.visibilityState === "visible",
      });
    }
  }

  private attachBrowserListeners(): void {
    if (typeof window === "undefined") {
      return;
    }

    const onOnline = () => {
      this.deps.dispatch({ type: "browser-online", at: this.deps.now() });
      this.bringProbeForward();
    };
    const onOffline = () => {
      this.deps.dispatch({ type: "browser-offline", at: this.deps.now() });
    };
    const onVisibility = () => {
      const visible =
        typeof document !== "undefined" &&
        document.visibilityState === "visible";
      this.deps.dispatch({
        type: "visibility-changed",
        at: this.deps.now(),
        visible,
      });
      if (visible) {
        // On return to the foreground, re-check — but the machine cannot alarm
        // without confirmed probe failures, so a throttled wake-up is safe.
        this.bringProbeForward();
      }
    };

    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    document.addEventListener("visibilitychange", onVisibility);
    this.listeners.push(
      () => window.removeEventListener("online", onOnline),
      () => window.removeEventListener("offline", onOffline),
      () => document.removeEventListener("visibilitychange", onVisibility),
    );
  }

  private async tick(force = false): Promise<void> {
    if (!this.running) {
      return;
    }

    const at = this.deps.now();
    this.deps.dispatch({ type: "evaluate", at });

    const state = this.deps.getState();
    // Never probe a hidden or offline tab: those failures are false negatives.
    // Nor a second time while one is still in flight; that is what the probe
    // is for.
    const canProbe = state.browserOnline && state.visible && !this.probing;
    // Probe when a real failure forced a verification, or (idle fallback) when
    // we lack fresh proof of reachability. Fresh real traffic already answers
    // the question, so an untriggered tick skips the synthetic load.
    if (canProbe && (force || this.isStale(state, at))) {
      await this.runProbe();
    }

    this.scheduleNext();
  }

  private isStale(state: ConnectivityState, now: number): boolean {
    return (
      state.lastReachableAt === null ||
      now - state.lastReachableAt >= STALE_AFTER_MS
    );
  }

  private async runProbe(): Promise<void> {
    const startedAt = this.deps.now();
    let ok = false;
    this.probing = true;
    try {
      ok = await this.deps.probe();
    } catch {
      ok = false;
    } finally {
      this.probing = false;
    }
    const at = this.deps.now();

    if (ok) {
      this.deps.dispatch({ type: "probe-succeeded", at });
      return;
    }

    // A failure the browser cannot vouch for is not counted. Either the tab was
    // suspended across the probe, so its abort was decided by a timer that
    // never ran, or it went to the background mid-probe, which the tick guard
    // already refuses to probe in. `evaluate` still advances the clock, so
    // staleness and the confirm cadence carry on as normal and the next probe,
    // run with a fair timeout, decides.
    const suspended = at - startedAt >= SUSPENDED_PROBE_AFTER_MS;
    if (suspended || !this.deps.getState().visible) {
      this.deps.dispatch({ type: "evaluate", at });
      return;
    }

    this.deps.dispatch({ type: "probe-failed", at });
  }

  private scheduleNext(): void {
    if (!this.running) {
      return;
    }
    this.clearTimer();
    const status = this.deps.getState().status;
    const delay =
      status === "online" ? FRESH_PROBE_INTERVAL_MS : CONFIRM_PROBE_INTERVAL_MS;
    this.timer = this.deps.setTimer(() => void this.tick(), delay);
  }

  private bringProbeForward(): void {
    if (!this.running) {
      return;
    }
    const state = this.deps.getState();
    if (!state.browserOnline || !state.visible) {
      return;
    }
    // A probe already in flight is the verification this would ask for, and
    // cancelling the tick that owns it would leave the driver with no timer.
    if (this.probing) {
      return;
    }
    this.clearTimer();
    // Force the probe: a real failure is a live signal something may be wrong
    // right now, so verify even if a recent success still looks "fresh".
    this.timer = this.deps.setTimer(() => void this.tick(true), REACTIVE_PROBE_DELAY_MS);
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      this.deps.clearTimer(this.timer);
      this.timer = null;
    }
  }
}

const defaultProbe = async (): Promise<boolean> => {
  if (typeof fetch === "undefined") {
    return false;
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const response = await fetch(LIVENESS_URL, {
      method: "GET",
      cache: "no-store",
      credentials: "omit",
      signal: controller.signal,
    });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timeout);
  }
};

let monitor: ConnectivityMonitor | null = null;

const getMonitor = (): ConnectivityMonitor => {
  if (!monitor) {
    monitor = new ConnectivityMonitor({
      dispatch: (event) => useConnectivityStore.getState().dispatch(event),
      getState: () => useConnectivityStore.getState(),
      now: () => Date.now(),
      probe: defaultProbe,
      setTimer: (fn, ms) => setTimeout(fn, ms) as unknown as TimerHandle,
      clearTimer: (handle) => clearTimeout(handle as unknown as ReturnType<typeof setTimeout>),
    });
  }
  return monitor;
};

export const startConnectivityMonitor = (): void => {
  if (typeof window === "undefined") {
    return;
  }
  getMonitor().start();
};

export const stopConnectivityMonitor = (): void => {
  if (typeof window === "undefined") {
    return;
  }
  getMonitor().stop();
};

/**
 * Feeds one observed request outcome into the monitor. Called from the single
 * axios response interceptor so every real request updates connectivity.
 */
export const recordRequestOutcome = (outcome: RequestOutcome): void => {
  if (typeof window === "undefined") {
    return;
  }
  getMonitor().recordRequestOutcome(outcome);
};
