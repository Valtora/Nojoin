import { beforeEach, describe, expect, it, vi } from "vitest";

const recordRequestOutcome = vi.fn();

vi.mock("@/lib/connectivity/monitor", () => ({
  recordRequestOutcome: (...args: unknown[]) => recordRequestOutcome(...args),
}));

describe("serviceStatusStore health content", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.restoreAllMocks();
    recordRequestOutcome.mockReset();
  });

  it("populates db/worker/version/warnings from the detailed health endpoint", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        version: "1.2.3",
        deployment_warnings: [
          {
            code: "placeholder_first_run_password",
            key: "FIRST_RUN_PASSWORD",
            title: "Placeholder bootstrap password configured",
            message: "Update it.",
          },
        ],
        components: {
          db: "connected",
          worker: "inactive",
        },
      }),
    } as Response);

    vi.stubGlobal("fetch", fetchMock);

    const { useServiceStatusStore } = await import("./serviceStatusStore");
    await useServiceStatusStore.getState().refreshHealth();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    // Reaching the endpoint at all reports reachability to the monitor.
    expect(recordRequestOutcome).toHaveBeenCalledWith({ reachedServer: true });
    expect(useServiceStatusStore.getState()).toMatchObject({
      db: true,
      worker: false,
      backendVersion: "1.2.3",
      deploymentWarnings: [
        {
          code: "placeholder_first_run_password",
          key: "FIRST_RUN_PASSWORD",
          title: "Placeholder bootstrap password configured",
          message: "Update it.",
        },
      ],
    });
  });

  it("does not fetch while the tab is hidden", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });

    try {
      const { useServiceStatusStore } = await import("./serviceStatusStore");
      await useServiceStatusStore.getState().refreshHealth();

      expect(fetchMock).not.toHaveBeenCalled();
      expect(recordRequestOutcome).not.toHaveBeenCalled();
    } finally {
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        get: () => "visible",
      });
    }
  });

  it("reports a transport failure to the monitor and keeps last-known content", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          version: "1.2.3",
          deployment_warnings: [],
          components: { db: "connected", worker: "active" },
        }),
      } as Response)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));

    vi.stubGlobal("fetch", fetchMock);

    const { useServiceStatusStore } = await import("./serviceStatusStore");
    await useServiceStatusStore.getState().refreshHealth();
    await useServiceStatusStore.getState().refreshHealth();

    expect(recordRequestOutcome).toHaveBeenCalledWith({ reachedServer: true });
    expect(recordRequestOutcome).toHaveBeenCalledWith({ reachedServer: false });
    // Content from the last good poll is preserved, not wiped to a false state.
    expect(useServiceStatusStore.getState()).toMatchObject({
      db: true,
      worker: true,
      backendVersion: "1.2.3",
    });
  });
});
