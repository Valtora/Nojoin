import { beforeEach, describe, expect, it, vi } from "vitest";

describe("serviceStatusStore backend health", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.restoreAllMocks();
  });

  it("uses the detailed health endpoint when it is available", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce({
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
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
      } as Response);

    vi.stubGlobal("fetch", fetchMock);

    const { useServiceStatusStore } = await import("./serviceStatusStore");

    await useServiceStatusStore.getState().checkBackend();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(useServiceStatusStore.getState()).toMatchObject({
      backend: true,
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
      backendFailCount: 0,
    });
  });

  it("falls back to the public health probe when the detailed endpoint fails", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
      } as Response);

    vi.stubGlobal("fetch", fetchMock);

    const { useServiceStatusStore } = await import("./serviceStatusStore");

    await useServiceStatusStore.getState().checkBackend();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(useServiceStatusStore.getState()).toMatchObject({
      backend: true,
      db: true,
      worker: true,
      deploymentWarnings: [],
      backendFailCount: 0,
    });
  });

  it("does not probe or accrue failures while the tab is hidden", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });

    try {
      const { useServiceStatusStore } = await import("./serviceStatusStore");

      await useServiceStatusStore.getState().checkBackend();

      // A backgrounded tab is throttled by the browser, so a timed-out probe
      // would be a false negative. We must not fetch, and must leave the
      // previously-known-good status untouched.
      expect(fetchMock).not.toHaveBeenCalled();
      expect(useServiceStatusStore.getState()).toMatchObject({
        backend: true,
        backendFailCount: 0,
      });
    } finally {
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        get: () => "visible",
      });
    }
  });

  it("preserves deployment warnings when falling back to the public health probe", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          version: "1.2.3",
          deployment_warnings: [
            {
              code: "placeholder_data_encryption_key",
              key: "DATA_ENCRYPTION_KEY",
              title: "Placeholder data encryption key configured",
              message: "Update it.",
            },
          ],
          components: {
            db: "connected",
            worker: "active",
          },
        }),
      } as Response)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
      } as Response);

    vi.stubGlobal("fetch", fetchMock);

    const { useServiceStatusStore } = await import("./serviceStatusStore");

    await useServiceStatusStore.getState().checkBackend();
    await useServiceStatusStore.getState().checkBackend();

    expect(useServiceStatusStore.getState().deploymentWarnings).toEqual([
      {
        code: "placeholder_data_encryption_key",
        key: "DATA_ENCRYPTION_KEY",
        title: "Placeholder data encryption key configured",
        message: "Update it.",
      },
    ]);
  });
});
