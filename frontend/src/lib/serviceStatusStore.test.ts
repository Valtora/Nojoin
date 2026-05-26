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
      backendFailCount: 0,
    });
  });
});