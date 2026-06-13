import { describe, expect, it, vi } from "vitest";

import { PickSourceError, pickCaptureSource } from "./pickSource";

const buildTrack = (kind: "audio" | "video", label: string, settings = {}) =>
  ({
    kind,
    label,
    enabled: true,
    muted: false,
    readyState: "live",
    stop: vi.fn(),
    getSettings: () => settings,
  }) as unknown as MediaStreamTrack;

const buildStream = (options: {
  audioTracks?: MediaStreamTrack[];
  videoTracks?: MediaStreamTrack[];
}) => {
  const audioTracks = options.audioTracks ?? [];
  const videoTracks = options.videoTracks ?? [];
  const tracks = [...audioTracks, ...videoTracks];
  return {
    getTracks: () => tracks,
    getAudioTracks: () => audioTracks,
    getVideoTracks: () => videoTracks,
  } as unknown as MediaStream;
};

describe("capture source picker", () => {
  it("uses display and microphone sources for shared-audio capture", async () => {
    const displayStream = buildStream({
      audioTracks: [buildTrack("audio", "System Audio")],
      videoTracks: [
        buildTrack("video", "Meeting Tab", { displaySurface: "browser" }),
      ],
    });
    const microphoneStream = buildStream({
      audioTracks: [
        buildTrack("audio", "USB Microphone", {
          deviceId: "mic-1",
          groupId: "group-1",
        }),
      ],
    });
    const mediaDevices = {
      getDisplayMedia: vi.fn().mockResolvedValue(displayStream),
      getUserMedia: vi.fn().mockResolvedValue(microphoneStream),
      enumerateDevices: vi.fn().mockResolvedValue([
        {
          kind: "audioinput",
          deviceId: "mic-1",
          label: "USB Microphone",
        },
      ]),
    } as unknown as MediaDevices;

    const sources = await pickCaptureSource({
      mode: "shared_audio",
      mediaDevices,
      microphoneDeviceId: "mic-1",
    });

    expect(sources.mode).toBe("shared_audio");
    expect(sources.displayStream).toBe(displayStream);
    expect(sources.microphoneStream).toBe(microphoneStream);
    expect(sources.captureReport.requested_microphone_device_id).toBe("mic-1");
    expect(sources.captureReport.shared_audio_available).toBe(true);
    expect(mediaDevices.getDisplayMedia).toHaveBeenCalledWith({
      video: true,
      audio: true,
      systemAudio: "include",
      windowAudio: "system",
      selfBrowserSurface: "include",
      surfaceSwitching: "include",
    });
    expect(mediaDevices.getUserMedia).toHaveBeenCalledWith({
      audio: {
        deviceId: { exact: "mic-1" },
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      },
    });
  });

  it("uses only the microphone source for microphone-only capture", async () => {
    const microphoneStream = buildStream({
      audioTracks: [buildTrack("audio", "Phone Microphone")],
    });
    const mediaDevices = {
      getDisplayMedia: vi.fn(),
      getUserMedia: vi.fn().mockResolvedValue(microphoneStream),
      enumerateDevices: vi.fn().mockResolvedValue([]),
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

  it("classifies a cancelled display picker as display_cancelled", async () => {
    const mediaDevices = {
      getDisplayMedia: vi.fn().mockRejectedValue(
        Object.assign(new Error("Permission denied by user"), {
          name: "NotAllowedError",
        }),
      ),
      getUserMedia: vi.fn(),
      enumerateDevices: vi.fn().mockResolvedValue([]),
    } as unknown as MediaDevices;

    await expect(
      pickCaptureSource({
        mode: "shared_audio",
        mediaDevices,
      }),
    ).rejects.toMatchObject<Partial<PickSourceError>>({
      code: "display_cancelled",
      message: "Display capture was cancelled before the recording started.",
    });

    expect(mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });

  it("fails closed when the selected microphone is unavailable", async () => {
    const mediaDevices = {
      getDisplayMedia: vi.fn(),
      getUserMedia: vi.fn(),
      enumerateDevices: vi.fn().mockResolvedValue([
        {
          kind: "audioinput",
          deviceId: "mic-2",
          label: "Fallback Microphone",
        },
      ]),
    } as unknown as MediaDevices;

    await expect(
      pickCaptureSource({
        mode: "microphone_only",
        mediaDevices,
        microphoneDeviceId: "mic-1",
      }),
    ).rejects.toMatchObject<Partial<PickSourceError>>({
      code: "selected_microphone_unavailable",
      message:
        "The selected microphone is not available to the browser. Choose another microphone in Settings > Capture before starting the recording.",
    });

    expect(mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });
});
