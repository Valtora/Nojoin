import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useLiveSpeakerNaming } from "./useLiveSpeakerNaming";

const nameLiveSpeaker = vi.fn();
const addNotification = vi.fn();

vi.mock("@/lib/api/speakers", () => ({
  nameLiveSpeaker: (...args: unknown[]) => nameLiveSpeaker(...args),
  getGlobalSpeakers: vi.fn(async () => []),
}));
vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

describe("useLiveSpeakerNaming", () => {
  it("names the marked line's explicit anchor revision and displays a settling conflict", async () => {
    nameLiveSpeaker.mockRejectedValue({ response: { status: 409, data: { detail: {
      code: "line_settling", message: "This line is still settling.",
    } } } });
    const applyNames = vi.fn();
    const { result } = renderHook(() => useLiveSpeakerNaming("rec-1", applyNames));
    await act(async () => result.current.renameLiveSpeaker("LIVE_PROVISIONAL_1", "Alex", {
      id: "u1", revision: 8, start: 0, end: 2, text: "Words", speaker: "LIVE_PROVISIONAL_1",
      settling_required: true, locked: true,
    }));
    expect(nameLiveSpeaker).toHaveBeenCalledWith("rec-1", "LIVE_PROVISIONAL_1", "Alex", { id: "u1", revision: 8 });
    expect(addNotification).toHaveBeenCalledWith({ type: "error", message: "This line is still settling." });
    expect(applyNames).not.toHaveBeenCalled();
  });
  beforeEach(() => {
    nameLiveSpeaker.mockReset();
    addNotification.mockReset();
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  it("shows the server's reason when a name is refused, and changes no name", async () => {
    nameLiveSpeaker.mockRejectedValue({
      response: {
        status: 422,
        data: { detail: "Pick a real name, or rename them to the right person" },
      },
    });
    const applyNames = vi.fn();
    const { result } = renderHook(() => useLiveSpeakerNaming("rec-1", applyNames));

    await act(async () => {
      await result.current.renameLiveSpeaker("LIVE_PROVISIONAL_1", "Speaker 2");
    });

    expect(addNotification).toHaveBeenCalledWith({
      type: "error",
      message: "Pick a real name, or rename them to the right person",
    });
    expect(applyNames).not.toHaveBeenCalled();
  });

  it("passes explicit create-person intent only with the locked source and refreshes after acceptance", async () => {
    nameLiveSpeaker.mockResolvedValue({ LIVE_PROVISIONAL_1: "Alex" });
    const refresh = vi.fn();
    const apply = vi.fn();
    const { result } = renderHook(() => useLiveSpeakerNaming("rec-1", apply, refresh));
    const segment = { id: "u1", revision: 8, start: 0, end: 2, text: "Words", speaker: "LIVE_PROVISIONAL_1",
      settling_required: true, locked: true };
    await act(async () => result.current.renameLiveSpeaker("LIVE_PROVISIONAL_1", "Alex", segment, true));
    expect(nameLiveSpeaker).toHaveBeenCalledWith("rec-1", "LIVE_PROVISIONAL_1", "Alex", { id: "u1", revision: 8 }, true);
    expect(apply).toHaveBeenCalledWith({ LIVE_PROVISIONAL_1: "Alex" });
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("sends an ordinary locked live source revision even when word settling is disabled", async () => {
    nameLiveSpeaker.mockResolvedValue({ LIVE_PROVISIONAL_1: "Alex" });
    const { result } = renderHook(() => useLiveSpeakerNaming("rec-1", vi.fn()));
    const segment = { id: "ordinary", revision: 3, start: 0, end: 2, text: "Words", speaker: "LIVE_PROVISIONAL_1",
      locked: true };
    await act(async () => result.current.renameLiveSpeaker("LIVE_PROVISIONAL_1", "Alex", segment));
    expect(nameLiveSpeaker).toHaveBeenLastCalledWith("rec-1", "LIVE_PROVISIONAL_1", "Alex", { id: "ordinary", revision: 3 });
    await act(async () => result.current.renameLiveSpeaker("LIVE_PROVISIONAL_1", "Alex", segment, true));
    expect(nameLiveSpeaker).toHaveBeenLastCalledWith("rec-1", "LIVE_PROVISIONAL_1", "Alex", { id: "ordinary", revision: 3 }, true);
  });

  it("refuses malformed locked source identity before sending creation intent to the API", async () => {
    nameLiveSpeaker.mockResolvedValue({});
    const refresh = vi.fn();
    const { result } = renderHook(() => useLiveSpeakerNaming("rec-1", vi.fn(), refresh));
    const malformed = { id: "", revision: 0, start: 0, end: 2, text: "Words", speaker: "LIVE_PROVISIONAL_1", locked: true };
    let accepted: unknown;
    await act(async () => { accepted = await result.current.renameLiveSpeaker("LIVE_PROVISIONAL_1", "Alex", malformed, true); });
    expect(accepted).toBe(false);
    expect(nameLiveSpeaker).not.toHaveBeenCalled();
    expect(refresh).not.toHaveBeenCalled();
  });

  it.each([undefined, { id: "u1", revision: 8, start: 0, end: 2, text: "Words", speaker: "LIVE_PROVISIONAL_1",
    settling_required: true, locked: false }])("refuses creation without a locked source before any request", async (segment) => {
    const refresh = vi.fn();
    const { result } = renderHook(() => useLiveSpeakerNaming("rec-1", vi.fn(), refresh));
    let accepted: unknown;
    await act(async () => { accepted = await result.current.renameLiveSpeaker("LIVE_PROVISIONAL_1", "Alex", segment, true); });
    expect(accepted).toBe(false);
    expect(nameLiveSpeaker).not.toHaveBeenCalled();
    expect(refresh).not.toHaveBeenCalled();
  });
});
