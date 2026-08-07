import { beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureWakeLock, type WakeLockSentinelLike } from "./wakeLock";

class FakeSentinel implements WakeLockSentinelLike {
  released = false;

  private listeners: Array<() => void> = [];

  release = vi.fn(async () => {
    this.released = true;
  });

  addEventListener(_type: "release", listener: () => void) {
    this.listeners.push(listener);
  }

  /** The browser taking the lock back, which it does whenever the tab hides. */
  dropFromBrowser() {
    this.released = true;
    this.listeners.forEach((listener) => listener());
  }
}

const buildDocument = () => {
  const listeners: Record<string, Array<() => void>> = {};
  return {
    visibilityState: "visible" as DocumentVisibilityState,
    addEventListener: vi.fn((type: string, listener: () => void) => {
      (listeners[type] ||= []).push(listener);
    }),
    removeEventListener: vi.fn((type: string, listener: () => void) => {
      listeners[type] = (listeners[type] || []).filter((l) => l !== listener);
    }),
    fire: (type: string) => (listeners[type] || []).forEach((l) => l()),
    listenerCount: (type: string) => (listeners[type] || []).length,
  };
};

describe("capture wake lock", () => {
  let sentinel: FakeSentinel;
  let request: ReturnType<typeof vi.fn>;
  let documentRef: ReturnType<typeof buildDocument>;

  beforeEach(() => {
    sentinel = new FakeSentinel();
    request = vi.fn(async () => sentinel);
    documentRef = buildDocument();
  });

  const build = () =>
    new CaptureWakeLock({
      request: request as unknown as (type: "screen") => Promise<WakeLockSentinelLike>,
      documentRef: documentRef as unknown as Document,
    });

  it("takes a screen lock when capture starts", async () => {
    const lock = build();
    await lock.acquire();

    expect(request).toHaveBeenCalledWith("screen");
    expect(lock.held).toBe(true);
  });

  it("releases the lock when capture stops", async () => {
    const lock = build();
    await lock.acquire();
    await lock.release();

    expect(sentinel.release).toHaveBeenCalled();
    expect(lock.held).toBe(false);
  });

  it("re-takes the lock when the tab returns to the foreground", async () => {
    // The browser drops the lock whenever the page stops being visible, so
    // without this a lock survives only until the first tab switch.
    const lock = build();
    await lock.acquire();
    sentinel.dropFromBrowser();

    const second = new FakeSentinel();
    request.mockResolvedValue(second);
    documentRef.visibilityState = "visible";
    documentRef.fire("visibilitychange");
    await Promise.resolve();

    expect(request).toHaveBeenCalledTimes(2);
  });

  it("does not ask again while the tab is hidden", async () => {
    const lock = build();
    await lock.acquire();
    documentRef.visibilityState = "hidden";
    documentRef.fire("visibilitychange");
    await Promise.resolve();

    expect(request).toHaveBeenCalledTimes(1);
  });

  it("does not stack locks when one is already held", async () => {
    const lock = build();
    await lock.acquire();
    documentRef.fire("visibilitychange");
    await Promise.resolve();

    expect(request).toHaveBeenCalledTimes(1);
  });

  it("stops listening once released", async () => {
    const lock = build();
    await lock.acquire();
    expect(documentRef.listenerCount("visibilitychange")).toBe(1);

    await lock.release();

    expect(documentRef.listenerCount("visibilitychange")).toBe(0);
  });

  it("survives a browser with no wake lock support", async () => {
    // Best-effort throughout: an unavailable lock must never fail a recording.
    const lock = new CaptureWakeLock({
      documentRef: documentRef as unknown as Document,
    });

    await expect(lock.acquire()).resolves.toBeUndefined();
    expect(lock.held).toBe(false);
    await expect(lock.release()).resolves.toBeUndefined();
  });

  it("survives a refused request", async () => {
    request.mockRejectedValue(new Error("denied by policy"));
    const lock = build();

    await expect(lock.acquire()).resolves.toBeUndefined();
    expect(lock.held).toBe(false);
  });

  it("tolerates releasing a lock the browser already dropped", async () => {
    const lock = build();
    await lock.acquire();
    sentinel.release.mockRejectedValue(new Error("already released"));

    await expect(lock.release()).resolves.toBeUndefined();
  });

  it("does not re-acquire after release", async () => {
    const lock = build();
    await lock.acquire();
    await lock.release();

    documentRef.fire("visibilitychange");
    await Promise.resolve();

    expect(request).toHaveBeenCalledTimes(1);
  });
});
