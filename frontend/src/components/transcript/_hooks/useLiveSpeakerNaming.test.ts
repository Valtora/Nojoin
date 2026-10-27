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
});
