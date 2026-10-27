import { act, renderHook, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { withProcessTimeZone } from "@/test/processTimeZone";

const getSettings = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ getSettings }));

// Each test starts from a page that has not loaded the setting yet.
const freshModules = async () => {
  vi.resetModules();
  const timezone = await import("./timezone");
  const { useUserTimeZone } = await import("./useUserTimeZone");
  return { ...timezone, useUserTimeZone };
};

describe("useUserTimeZone", () => {
  beforeEach(() => {
    getSettings.mockReset();
    getSettings.mockResolvedValue({ timezone: "Asia/Tokyo" });
  });

  it("renders UTC in server output, as the client's first render must", async () => {
    const { setCachedUserTimeZone, useUserTimeZone } = await freshModules();
    setCachedUserTimeZone("Asia/Tokyo");
    function Probe() {
      return <span>{useUserTimeZone()}</span>;
    }

    expect(renderToString(<Probe />)).toContain("UTC");
  });

  it("loads the setting once, then shows it", async () => {
    const { useUserTimeZone } = await freshModules();

    const first = renderHook(() => useUserTimeZone());
    expect(first.result.current).toBe("UTC");
    await waitFor(() => expect(first.result.current).toBe("Asia/Tokyo"));

    const second = renderHook(() => useUserTimeZone());
    expect(second.result.current).toBe("Asia/Tokyo");
    expect(getSettings).toHaveBeenCalledTimes(1);
  });

  it("follows a newly saved setting without remounting", async () => {
    const { setCachedUserTimeZone, useUserTimeZone } = await freshModules();
    setCachedUserTimeZone("Asia/Tokyo");
    const { result } = renderHook(() => useUserTimeZone());
    expect(result.current).toBe("Asia/Tokyo");

    act(() => {
      setCachedUserTimeZone("America/New_York");
    });

    expect(result.current).toBe("America/New_York");
    expect(getSettings).not.toHaveBeenCalled();
  });
});

describe("a setting that cannot be loaded", () => {
  // The browser's zone, which stands in when the setting cannot be loaded.
  withProcessTimeZone("America/New_York");

  beforeEach(() => {
    getSettings.mockReset();
    getSettings
      .mockRejectedValueOnce(new Error("Network Error"))
      .mockResolvedValue({ timezone: "Asia/Tokyo" });
  });

  it("falls back to the browser's zone without caching it, so the next call asks again", async () => {
    const { getCachedUserTimeZone, getUserTimeZone } = await freshModules();

    expect(await getUserTimeZone()).toBe("America/New_York");
    expect(getCachedUserTimeZone()).toBeNull();

    expect(await getUserTimeZone()).toBe("Asia/Tokyo");
    expect(getCachedUserTimeZone()).toBe("Asia/Tokyo");
    expect(getSettings).toHaveBeenCalledTimes(2);
  });

  it("shows the browser's zone meanwhile, and the setting once a later view loads it", async () => {
    const { useUserTimeZone } = await freshModules();

    const first = renderHook(() => useUserTimeZone());
    await waitFor(() => expect(first.result.current).toBe("America/New_York"));

    const second = renderHook(() => useUserTimeZone());
    await waitFor(() => expect(second.result.current).toBe("Asia/Tokyo"));
    expect(first.result.current).toBe("Asia/Tokyo");
  });
});
