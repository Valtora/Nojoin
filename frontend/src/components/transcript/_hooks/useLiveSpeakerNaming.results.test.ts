import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useLiveSpeakerNaming } from "./useLiveSpeakerNaming";

const { nameLiveSpeaker } = vi.hoisted(() => ({ nameLiveSpeaker: vi.fn() }));
vi.mock("@/lib/api/speakers", () => ({ nameLiveSpeaker, getGlobalSpeakers: vi.fn(async () => []) }));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: vi.fn() }) }));
afterEach(() => { vi.clearAllMocks(); vi.restoreAllMocks(); });

describe("live naming outcomes", () => {
  it("returns refusal when the server rejects naming so the editor can retain its draft", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    nameLiveSpeaker.mockRejectedValue(new Error("Stale anchor"));
    const { result } = renderHook(() => useLiveSpeakerNaming("r1", vi.fn()));
    let accepted: unknown;
    await act(async () => { accepted = await result.current.renameLiveSpeaker("LIVE_PROVISIONAL_1", "Alex"); });
    expect(accepted).toBe(false);
  });

  it("returns acceptance only after applying the accepted names", async () => {
    const names = { LIVE_PROVISIONAL_1: "Alex" };
    nameLiveSpeaker.mockResolvedValue(names);
    const apply = vi.fn();
    const { result } = renderHook(() => useLiveSpeakerNaming("r1", apply));
    let accepted: unknown;
    await act(async () => { accepted = await result.current.renameLiveSpeaker("LIVE_PROVISIONAL_1", "Alex"); });
    expect(apply).toHaveBeenCalledWith(names);
    expect(accepted).toBe(true);
  });
});
