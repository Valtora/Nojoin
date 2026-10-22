import { afterEach, describe, expect, it, vi } from "vitest";

import { holdCaptureLock, isCaptureHeldInThisBrowser } from "./captureLock";

/**
 * A minimal LockManager: enough of the Web Locks API to show that a held lock
 * is visible to a query from any tab and is gone once its holder lets go.
 */
const installLockManager = () => {
  const held = new Map<string, number>();
  const locks = {
    request: vi.fn(
      async (
        name: string,
        _options: LockOptions,
        callback: () => Promise<void>,
      ) => {
        held.set(name, (held.get(name) ?? 0) + 1);
        try {
          await callback();
        } finally {
          const count = (held.get(name) ?? 1) - 1;
          if (count > 0) {
            held.set(name, count);
          } else {
            held.delete(name);
          }
        }
      },
    ),
    query: vi.fn(async () => ({
      held: [...held.keys()].map((name) => ({ name, mode: "shared" })),
      pending: [],
    })),
  };
  vi.stubGlobal("navigator", { ...navigator, locks });
  return locks;
};

describe("capture lock", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a capture as live in this browser until its tab lets go", async () => {
    const locks = installLockManager();

    const release = holdCaptureLock("rec-1");

    await expect(isCaptureHeldInThisBrowser("rec-1")).resolves.toBe(true);
    await expect(isCaptureHeldInThisBrowser("rec-2")).resolves.toBe(false);
    // Shared, so taking it never waits behind another holder.
    expect(locks.request).toHaveBeenCalledWith(
      "nojoin-capture:rec-1",
      { mode: "shared" },
      expect.any(Function),
    );

    release();
    await new Promise((resolve) => setTimeout(resolve, 0));

    await expect(isCaptureHeldInThisBrowser("rec-1")).resolves.toBe(false);
  });

  it("reports nothing held where the browser has no Web Locks", async () => {
    vi.stubGlobal("navigator", { ...navigator, locks: undefined });

    const release = holdCaptureLock("rec-1");

    await expect(isCaptureHeldInThisBrowser("rec-1")).resolves.toBe(false);
    expect(release).not.toThrow();
  });

  it("reports nothing held when the browser refuses the query", async () => {
    const locks = installLockManager();
    locks.query.mockRejectedValue(new Error("SecurityError"));

    await expect(isCaptureHeldInThisBrowser("rec-1")).resolves.toBe(false);
  });
});
