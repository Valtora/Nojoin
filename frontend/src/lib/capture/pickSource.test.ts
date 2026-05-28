import { describe, expect, it, vi } from "vitest";

import { pickCaptureSource } from "./pickSource";

const buildStream = (audioTrackCount: number) => {
  const tracks = Array.from({ length: audioTrackCount }, () => ({
    stop: vi.fn(),
  }));

  return {
    getTracks: () => tracks,
    getAudioTracks: () => tracks,
  } as unknown as MediaStream;
};

describe("capture source picker", () => {
  it("uses display and microphone sources for shared-audio capture", async () => {
    const displayStream = buildStream(1);
    const microphoneStream = buildStream(1);
    const mediaDevices = {
      getDisplayMedia: vi.fn().mockResolvedValue(displayStream),
      getUserMedia: vi.fn().mockResolvedValue(microphoneStream),
    } as unknown as MediaDevices;

    const sources = await pickCaptureSource({
      mode: "shared_audio",
      mediaDevices,
    });

    expect(sources.mode).toBe("shared_audio");
    expect(sources.displayStream).toBe(displayStream);
    expect(sources.microphoneStream).toBe(microphoneStream);
    expect(mediaDevices.getDisplayMedia).toHaveBeenCalledTimes(1);
    expect(mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
  });

  it("uses only the microphone source for microphone-only capture", async () => {
    const microphoneStream = buildStream(1);
    const mediaDevices = {
      getDisplayMedia: vi.fn(),
      getUserMedia: vi.fn().mockResolvedValue(microphoneStream),
    } as unknown as MediaDevices;

    const sources = await pickCaptureSource({
      mode: "microphone_only",
      mediaDevices,
    });

    expect(sources.mode).toBe("microphone_only");
    expect(sources.displayStream).toBeNull();
    expect(sources.microphoneStream).toBe(microphoneStream);
    expect(mediaDevices.getDisplayMedia).not.toHaveBeenCalled();
    expect(mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
  });
});
