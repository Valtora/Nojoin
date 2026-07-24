import { describe, expect, it } from "vitest";

import {
  createInitialState,
  isReachable,
  PROBE_FAILURES_TO_UNREACHABLE,
  reduce,
  STALE_AFTER_MS,
  type ConnectivityEvent,
  type ConnectivityState,
} from "./reducer";

const T0 = 1_000_000; // arbitrary fixed clock; the reducer never reads a real clock

const run = (
  events: ConnectivityEvent[],
  start: ConnectivityState = createInitialState(),
): ConnectivityState => events.reduce(reduce, start);

describe("connectivity reducer", () => {
  it("starts optimistic-online so a fresh load never flashes a false alarm", () => {
    const state = createInitialState();
    expect(state.status).toBe("online");
    expect(isReachable(state.status)).toBe(true);
  });

  it("treats any server response as proof of life", () => {
    const state = run([{ type: "request-succeeded", at: T0 }]);
    expect(state.status).toBe("online");
    expect(state.lastReachableAt).toBe(T0);
    expect(state.probeFailureStreak).toBe(0);
  });

  it("goes stale to `checking` (never straight to unreachable) after the freshness window", () => {
    const state = run([
      { type: "request-succeeded", at: T0 },
      { type: "evaluate", at: T0 + STALE_AFTER_MS + 1 },
    ]);
    expect(state.status).toBe("checking");
    expect(isReachable(state.status)).toBe(true); // still usable, just verifying
  });

  it("escalates to `unreachable` only after enough probe failures while stale", () => {
    const events: ConnectivityEvent[] = [{ type: "request-succeeded", at: T0 }];
    for (let i = 0; i < PROBE_FAILURES_TO_UNREACHABLE; i += 1) {
      events.push({ type: "probe-failed", at: T0 + STALE_AFTER_MS + 1 + i });
    }
    const state = run(events);
    expect(state.probeFailureStreak).toBe(PROBE_FAILURES_TO_UNREACHABLE);
    expect(state.status).toBe("unreachable");
  });

  it("does not alarm at exactly one below the threshold", () => {
    const events: ConnectivityEvent[] = [{ type: "request-succeeded", at: T0 }];
    for (let i = 0; i < PROBE_FAILURES_TO_UNREACHABLE - 1; i += 1) {
      events.push({ type: "probe-failed", at: T0 + STALE_AFTER_MS + 1 + i });
    }
    const state = run(events);
    expect(state.status).toBe("checking");
  });

  it("recovers instantly on any success after being unreachable", () => {
    const events: ConnectivityEvent[] = [{ type: "request-succeeded", at: T0 }];
    for (let i = 0; i < PROBE_FAILURES_TO_UNREACHABLE; i += 1) {
      events.push({ type: "probe-failed", at: T0 + STALE_AFTER_MS + 1 + i });
    }
    events.push({ type: "probe-succeeded", at: T0 + 100_000 });
    const state = run(events);
    expect(state.status).toBe("online");
    expect(state.probeFailureStreak).toBe(0);
  });

  it("browser-offline is its own state, distinct from unreachable", () => {
    const state = run([{ type: "browser-offline", at: T0 }]);
    expect(state.status).toBe("offline");
    expect(isReachable(state.status)).toBe(false);
  });

  it("a successful request overrides a stale offline flag (we clearly are online)", () => {
    const state = run([
      { type: "browser-offline", at: T0 },
      { type: "request-succeeded", at: T0 + 10 },
    ]);
    expect(state.status).toBe("online");
    expect(state.browserOnline).toBe(true);
  });

  // The incident: during a recording, segment uploads keep succeeding, so the
  // machine must stay online across a long span even with an interleaved miss.
  it("stays online while real traffic keeps succeeding (the recording case)", () => {
    const events: ConnectivityEvent[] = [];
    for (let t = 0; t <= 300_000; t += 4_000) {
      // A single blocked socket in the middle must not matter.
      events.push(
        t === 120_000
          ? { type: "request-failed", at: T0 + t, gateway: false }
          : { type: "request-succeeded", at: T0 + t },
      );
    }
    const state = run(events);
    expect(state.status).toBe("online");
  });

  // The wake-up burst: a hidden→visible flip plus a couple of failed real
  // requests must NOT alarm without confirmed probe failures.
  it("a visibility flip with failed requests does not alarm without probe failures", () => {
    const state = run([
      { type: "request-succeeded", at: T0 },
      { type: "visibility-changed", at: T0 + 60_000, visible: false },
      { type: "visibility-changed", at: T0 + 90_000, visible: true },
      { type: "request-failed", at: T0 + 90_001, gateway: false },
      { type: "request-failed", at: T0 + 90_002, gateway: true },
    ]);
    expect(state.status).toBe("checking");
    expect(state.status).not.toBe("unreachable");
  });

  it("never probes a hidden tab into unreachable (probe failures still counted only when driver runs them)", () => {
    // Sanity: visibility does not itself change reachability derivation.
    const visible = run([{ type: "visibility-changed", at: T0, visible: true }]);
    const hidden = run([{ type: "visibility-changed", at: T0, visible: false }]);
    expect(visible.status).toBe("online");
    expect(hidden.status).toBe("online");
  });
});
