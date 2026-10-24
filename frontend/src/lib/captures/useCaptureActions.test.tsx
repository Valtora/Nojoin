import { act, fireEvent, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const track = { readyState: "live" } as MediaStreamTrack;
const capture = {
  runtimeActive: true,
  displayVideoTrack: vi.fn((): MediaStreamTrack | null => track),
};
vi.mock("@/lib/capture/CaptureProvider", () => ({ useCapture: () => capture }));

const grabDisplayFrame = vi.fn(async () => new Blob(["png"], { type: "image/png" }));
const imageForCapture = vi.fn(async (image: Blob) => image);
vi.mock("./frameImage", () => ({
  grabDisplayFrame: (...args: unknown[]) => grabDisplayFrame(...(args as [])),
  imageForCapture: (image: Blob) => imageForCapture(image),
}));

const add = vi.fn();
vi.mock("./captureOutbox", () => ({ captureOutbox: { add: (...args: unknown[]) => add(...args) } }));

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
    const { result } = renderHook(() => useCaptureActions("rec-1", readAudioNow));
    expect(result.current.canCaptureScreen).toBe(true);

    await act(async () => {
      press({ code: "KeyC", key: "Ç", altKey: true, shiftKey: true });
      nowMs = 70_000;
    });

    expect(grabDisplayFrame).toHaveBeenCalledWith(track);
    expect(add).toHaveBeenCalledWith("rec-1", expect.any(Blob), 65_000);
    expect(result.current.message).toMatchObject({ text: "Capture taken at 01:05.", tone: "info" });
  });

  it("offers nothing, and listens for nothing, without a shared screen", async () => {
    capture.displayVideoTrack.mockImplementation(() => null);
    const { result } = renderHook(() => useCaptureActions("rec-1", readAudioNow));

    await act(async () => {
      press({ code: "KeyC", altKey: true, shiftKey: true });
    });

    expect(result.current.canCaptureScreen).toBe(false);
    expect(grabDisplayFrame).not.toHaveBeenCalled();
  });

  it("stores a pasted image even without a shared screen", async () => {
    capture.runtimeActive = false;
    const { result } = renderHook(() => useCaptureActions("rec-1", readAudioNow));
    const image = new Blob(["jpeg"], { type: "image/jpeg" });

    await act(() => result.current.addImage(image));

    expect(add).toHaveBeenCalledWith("rec-1", image, 65_000);
  });

  it("says why a still could not be taken", async () => {
    grabDisplayFrame.mockRejectedValueOnce(new Error("The shared screen sent no picture."));
    const { result } = renderHook(() => useCaptureActions("rec-1", readAudioNow));

    await act(() => result.current.captureScreen());

    expect(add).not.toHaveBeenCalled();
    expect(result.current.message).toMatchObject({
      text: "The shared screen sent no picture.",
      tone: "error",
    });
  });
});
