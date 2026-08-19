import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectivityMonitor, type MonitorDeps } from "./monitor";
import {
  createInitialState,
  PROBE_FAILURES_TO_UNREACHABLE,
  reduce,
  STALE_AFTER_MS,
  type ConnectivityEvent,
} from "./reducer";

// A local reducer-backed store so the driver exercises the real state machine.
const makeHarness = (probe: () => Promise<boolean>) => {
  let state = createInitialState();
  const deps: MonitorDeps = {
    dispatch: (event: ConnectivityEvent) => {
      state = reduce(state, event);
    },
    getState: () => state,
    now: () => Date.now(),
    probe,
    setTimer: (fn, ms) => setTimeout(fn, ms) as unknown as number,
    clearTimer: (handle) => clearTimeout(handle as unknown as ReturnType<typeof setTimeout>),
  };
  return { monitor: new ConnectivityMonitor(deps), getState: () => state };
};

describe("ConnectivityMonitor driver", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("probes on start and reports online when the backend answers", async () => {
    const probe = vi.fn().mockResolvedValue(true);
    const { monitor, getState } = makeHarness(probe);

    monitor.start();
    await vi.advanceTimersByTimeAsync(1);

    expect(probe).toHaveBeenCalledTimes(1);
    expect(getState().status).toBe("online");
    monitor.stop();
  });

  it("does not fire synthetic probes while real traffic keeps it fresh", async () => {
    const probe = vi.fn().mockResolvedValue(true);
    const { monitor, getState } = makeHarness(probe);

    monitor.start();
    await vi.advanceTimersByTimeAsync(1); // initial probe (1)

    // Simulate steady real traffic every 4s for 60s (the recording case).
    for (let i = 0; i < 15; i += 1) {
      monitor.recordRequestOutcome({ reachedServer: true });
      await vi.advanceTimersByTimeAsync(4_000);
    }

    // Only the initial probe should have run; fresh real traffic skips probing.
    expect(probe).toHaveBeenCalledTimes(1);
    expect(getState().status).toBe("online");
    monitor.stop();
  });

  it("confirms unreachable only after repeated probe failures once stale", async () => {
    const probe = vi.fn().mockResolvedValue(false);
    const { monitor, getState } = makeHarness(probe);

    monitor.start();
    // Advance well past staleness; the confirm-cadence prober keeps failing.
    await vi.advanceTimersByTimeAsync(STALE_AFTER_MS + 5_000 * PROBE_FAILURES_TO_UNREACHABLE + 100);

    expect(probe.mock.calls.length).toBeGreaterThanOrEqual(PROBE_FAILURES_TO_UNREACHABLE);
    expect(getState().status).toBe("unreachable");
    monitor.stop();
  });

  it("recovers to online once a probe succeeds again", async () => {
    const probe = vi.fn().mockResolvedValue(false);
    const { monitor, getState } = makeHarness(probe);

    monitor.start();
    await vi.advanceTimersByTimeAsync(STALE_AFTER_MS + 5_000 * PROBE_FAILURES_TO_UNREACHABLE + 100);
    expect(getState().status).toBe("unreachable");

    probe.mockResolvedValue(true);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(getState().status).toBe("online");
    monitor.stop();
  });

  it("never runs two probes at once, however many requests fail", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    // A probe that never resolves on its own, standing in for one stalled
    // across a tab suspension.
    const release: Array<(ok: boolean) => void> = [];
    const probe = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          inFlight += 1;
          maxInFlight = Math.max(maxInFlight, inFlight);
          release.push((ok) => {
            inFlight -= 1;
            resolve(ok);
          });
        }),
    );
    const { monitor, getState } = makeHarness(probe);

    monitor.start();
    await vi.advanceTimersByTimeAsync(1);

    // The resume storm: a burst of failed requests, each asking for a probe.
    for (let i = 0; i < 8; i += 1) {
      monitor.recordRequestOutcome({ reachedServer: false });
      await vi.advanceTimersByTimeAsync(600);
    }

    expect(maxInFlight).toBe(1);
    expect(probe).toHaveBeenCalledTimes(1);
    // Nothing has been confirmed, so nothing is claimed.
    expect(getState().status).not.toBe("unreachable");

    release.forEach((resolve) => resolve(true));
    monitor.stop();
  });

  it("does not count a probe that was suspended rather than answered", async () => {
    // Fails, but only after far longer than its own timeout, which is what a
    // tab frozen across the probe looks like: the abort fires on thaw.
    const probe = vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 120_000));
      return false;
    });
    const { monitor, getState } = makeHarness(probe);

    monitor.start();
    await vi.advanceTimersByTimeAsync(STALE_AFTER_MS + 5_000 * PROBE_FAILURES_TO_UNREACHABLE + 200_000);

    expect(probe.mock.calls.length).toBeGreaterThanOrEqual(1);
    expect(getState().probeFailureStreak).toBe(0);
    expect(getState().status).not.toBe("unreachable");
    monitor.stop();
  });

  it("brings a probe forward when a real request fails", async () => {
    const probe = vi.fn().mockResolvedValue(true);
    const { monitor, getState } = makeHarness(probe);

    monitor.start();
    await vi.advanceTimersByTimeAsync(1);
    expect(probe).toHaveBeenCalledTimes(1);

    monitor.recordRequestOutcome({ reachedServer: false, gateway: true });
    await vi.advanceTimersByTimeAsync(600); // reactive probe (~500ms)

    expect(probe).toHaveBeenCalledTimes(2);
    expect(getState().status).toBe("online");
    monitor.stop();
  });
});
