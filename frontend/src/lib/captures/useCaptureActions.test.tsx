import { act, fireEvent, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const track = { readyState: "live" } as MediaStreamTrack;
const capture = {
  runtimeActive: true,
  recordingId: "rec-1",
  displayVideoTrack: vi.fn((): MediaStreamTrack | null => track),
  support: { supported: true, mode: "shared_audio" },
};
vi.mock("@/lib/capture/CaptureProvider", () => ({ useCapture: () => capture }));

const grabDisplayFrame = vi.fn(async () => new Blob(["png"], { type: "image/png" }));
vi.mock("./frameImage", () => ({
  grabDisplayFrame: (...args: unknown[]) => grabDisplayFrame(...(args as [])),
}));

const add = vi.fn<(file: File, atMs: number) => string | null>(() => null);

const { isCaptureShortcut, useCaptureActions } = await import("./useCaptureActions");

const press = (init: KeyboardEventInit) => fireEvent.keyDown(window, init);

describe("taking captures during a recording", () => {
  let nowMs = 65_000;
  const readAudioNow = () => nowMs;

  beforeEach(() => {
    nowMs = 65_000;
    add.mockClear();
    grabDisplayFrame.mockClear();
    capture.runtimeActive = true;
    capture.recordingId = "rec-1";
    capture.support = { supported: true, mode: "shared_audio" };
    capture.displayVideoTrack.mockImplementation(() => track);
  });

  it("knows its shortcut by key position, with no Control or Command", () => {
    const base = { code: "KeyC", altKey: true, shiftKey: true, ctrlKey: false, metaKey: false };

    expect(isCaptureShortcut(base)).toBe(true);
    expect(isCaptureShortcut({ ...base, ctrlKey: true })).toBe(false);
    expect(isCaptureShortcut({ ...base, metaKey: true })).toBe(false);
    expect(isCaptureShortcut({ ...base, shiftKey: false })).toBe(false);
    expect(isCaptureShortcut({ ...base, code: "KeyV" })).toBe(false);
  });

  it("takes a still on Alt+Shift+C, at the time the keys were pressed", async () => {
    const { result } = renderHook(() => useCaptureActions("rec-1", readAudioNow, add));
    expect(result.current.canCaptureScreen).toBe(true);
    expect(result.current.screenCaptureHint).toBeNull();

    await act(async () => {
      press({ code: "KeyC", key: "Ç", altKey: true, shiftKey: true });
      nowMs = 70_000;
    });

    expect(grabDisplayFrame).toHaveBeenCalledWith(track);
    expect(add).toHaveBeenCalledWith(expect.any(File), 65_000);
    expect(add.mock.calls[0][0].name).toBe("screen-capture.png");
    expect(result.current.message).toMatchObject({ text: "Screen captured at 01:05.", tone: "info" });
  });

  it("offers nothing, and listens for nothing, without a shared screen", async () => {
    capture.displayVideoTrack.mockImplementation(() => null);
    const { result } = renderHook(() => useCaptureActions("rec-1", readAudioNow, add));

    await act(async () => {
      press({ code: "KeyC", altKey: true, shiftKey: true });
      await result.current.captureScreen();
    });

    expect(result.current.canCaptureScreen).toBe(false);
    expect(result.current.screenCaptureHint).toBe("Screen captures need a shared tab, window or screen.");
    expect(grabDisplayFrame).not.toHaveBeenCalled();
    expect(add).not.toHaveBeenCalled();
  });

  it("does not show a missing-share hint for another recording, on a phone or after capture ends", () => {
    capture.displayVideoTrack.mockReturnValue(null);
    const { result, rerender } = renderHook(() => useCaptureActions("rec-2", readAudioNow, add));
    expect(result.current.screenCaptureHint).toBeNull();
    capture.recordingId = "rec-2";
    capture.support = { supported: true, mode: "microphone_only" };
    rerender();
    expect(result.current.screenCaptureHint).toBeNull();
    capture.support = { supported: true, mode: "shared_audio" };
    capture.runtimeActive = false;
    rerender();
    expect(result.current.screenCaptureHint).toBeNull();
  });

  it("says why the note for a still was refused", async () => {
    add.mockReturnValueOnce("There is no point in the recording to anchor this note to yet.");
    const { result } = renderHook(() => useCaptureActions("rec-1", readAudioNow, add));

    await act(() => result.current.captureScreen());

    expect(result.current.message).toMatchObject({
      text: "There is no point in the recording to anchor this note to yet.",
      tone: "error",
    });
  });

  it("says why a still could not be taken", async () => {
    grabDisplayFrame.mockRejectedValueOnce(new Error("The shared screen sent no picture."));
    const { result } = renderHook(() => useCaptureActions("rec-1", readAudioNow, add));

    await act(() => result.current.captureScreen());

    expect(add).not.toHaveBeenCalled();
    expect(result.current.message).toMatchObject({
      text: "The shared screen sent no picture.",
      tone: "error",
    });
  });
});
