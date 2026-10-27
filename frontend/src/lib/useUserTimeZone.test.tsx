import { act, renderHook, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
