import { beforeEach, describe, expect, it, vi } from "vitest";

describe("serviceStatusStore companion pairing state", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.restoreAllMocks();
  });

  it("keeps the device paired when backend auth succeeds but the local status endpoint is unreachable", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ token: "local-token", expires_in: 120 }),
      } as Response)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));

    vi.stubGlobal("fetch", fetchMock);

    const { useServiceStatusStore } = await import("./serviceStatusStore");

    await useServiceStatusStore.getState().checkCompanion();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(useServiceStatusStore.getState()).toMatchObject({
      companion: false,
      companionAuthenticated: true,
      companionLocalConnectionUnavailable: true,
      companionFailCount: 1,
    });
  });

  it("keeps the device paired when the local status endpoint returns a non-auth failure", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ token: "local-token", expires_in: 120 }),
      } as Response)
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
      } as Response);

    vi.stubGlobal("fetch", fetchMock);

    const { useServiceStatusStore } = await import("./serviceStatusStore");

    await useServiceStatusStore.getState().checkCompanion();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(useServiceStatusStore.getState()).toMatchObject({
      companion: false,
      companionAuthenticated: true,
      companionLocalConnectionUnavailable: false,
      companionFailCount: 1,
    });
  });
});