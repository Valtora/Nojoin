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
