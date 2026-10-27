// @vitest-environment node

import { describe, expect, it, vi } from "vitest";

import { PickSourceError, pickCaptureSource } from "./pickSource";
import { DEFAULT_CAPTURE_SETTINGS } from "./shared";

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
  it.each([
    ["NotReadableError", "microphone_busy", /busy.*close.*resume/i],
    ["NotFoundError", "microphone_unavailable", /unavailable.*connect.*resume/i],
    ["NotAllowedError", "microphone_denied", /permission.*allow.*resume/i],
  ])("reports a cold released-microphone %s with its specific reason and recovery message", async (
    name, code, message,
  ) => {
    const mediaDevices = {
      enumerateDevices: vi.fn().mockResolvedValue([{ kind: "audioinput", deviceId: "mic-1" }]),
      getUserMedia: vi.fn().mockRejectedValue(Object.assign(new Error("native capture failure"), { name })),
    } as unknown as MediaDevices;

    await expect(pickCaptureSource({
      mode: "microphone_only",
      microphoneDeviceId: "mic-1",
      resumeReleasedMicrophone: true,
      settings: DEFAULT_CAPTURE_SETTINGS,
      mediaDevices,
    })).rejects.toMatchObject({
      code,
      message: expect.stringMatching(message),
      captureReport: { notes: [code === "microphone_denied" ? "microphone_capture_denied" : code] },
    });
    expect(mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["NotReadableError", "microphone_denied", /native capture failure/i],
    ["NotFoundError", "selected_microphone_unavailable", /before starting the recording/i],
    ["NotAllowedError", "microphone_denied", /native capture failure/i],
  ])("keeps ordinary source-picking classification and messages unchanged for %s", async (name, code, message) => {
    const mediaDevices = {
      enumerateDevices: vi.fn().mockResolvedValue([{ kind: "audioinput", deviceId: "mic-1" }]),
      getUserMedia: vi.fn().mockRejectedValue(Object.assign(new Error("native capture failure"), { name })),
    } as unknown as MediaDevices;

    await expect(pickCaptureSource({
      mode: "microphone_only",
      microphoneDeviceId: "mic-1",
      settings: DEFAULT_CAPTURE_SETTINGS,
      mediaDevices,
    })).rejects.toMatchObject({ code, message: expect.stringMatching(message) });
  });

  it("falls back with a source-report note only for a released microphone resume", async () => {
    const fallback = buildTrack("audio", "Default", { deviceId: "mic-2" });
    const mediaDevices = {
      enumerateDevices: vi.fn().mockResolvedValue([
        { kind: "audioinput", deviceId: "mic-2", label: "Default" },
      ]),
      getUserMedia: vi.fn().mockResolvedValue(buildStream({ audioTracks: [fallback] })),
    } as unknown as MediaDevices;

    const sources = await pickCaptureSource({
      mode: "microphone_only",
      microphoneDeviceId: "mic-1",
      resumeReleasedMicrophone: true,
      settings: DEFAULT_CAPTURE_SETTINGS,
      mediaDevices,
    });

    expect(sources.microphoneStream.getAudioTracks()).toEqual([fallback]);
    expect(sources.captureReport.notes).toContain("microphone_default_fallback");
    expect(mediaDevices.getUserMedia).toHaveBeenCalledWith({
      audio: {
        deviceId: undefined,
        echoCancellation: true,
        noiseSuppression: false,
        autoGainControl: true,
      },
    });
  });

  it("keeps a cold released-microphone resume busy error actionable and cleans up display", async () => {
    const display = buildTrack("audio", "Shared");
    const mediaDevices = {
      enumerateDevices: vi.fn().mockResolvedValue([{ kind: "audioinput", deviceId: "mic-1" }]),
      getDisplayMedia: vi.fn().mockResolvedValue(buildStream({ audioTracks: [display] })),
      getUserMedia: vi.fn().mockRejectedValue(Object.assign(new Error("busy native error"), {
        name: "NotReadableError",
      })),
    } as unknown as MediaDevices;

    await expect(pickCaptureSource({
      mode: "shared_audio",
      microphoneDeviceId: "mic-1",
      resumeReleasedMicrophone: true,
      settings: DEFAULT_CAPTURE_SETTINGS,
      mediaDevices,
    })).rejects.toThrow(/busy.*close.*resume/i);
    expect(display.stop).toHaveBeenCalledOnce();
  });

  it("releases its current microphone after a paused capture replaces the stream", async () => {
    const original = buildTrack("audio", "Original");
    const replacement = buildTrack("audio", "Replacement");
    const display = buildTrack("audio", "Shared");
    const sources = await pickCaptureSource({
      mode: "shared_audio",
      settings: DEFAULT_CAPTURE_SETTINGS,
      mediaDevices: {
        enumerateDevices: vi.fn().mockResolvedValue([]),
        getDisplayMedia: vi.fn().mockResolvedValue(buildStream({ audioTracks: [display] })),
        getUserMedia: vi.fn().mockResolvedValue(buildStream({ audioTracks: [original] })),
      } as unknown as MediaDevices,
    });
    original.stop();
    sources.microphoneStream = buildStream({ audioTracks: [replacement] });

    sources.release();

    expect(replacement.stop).toHaveBeenCalledOnce();
    expect(display.stop).toHaveBeenCalledOnce();
    expect(original.stop).toHaveBeenCalledOnce();
  });

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
      settings: {
        ...DEFAULT_CAPTURE_SETTINGS,
        microphoneGain: 1.4,
        systemGain: 0.9,
      },
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
          echoCancellation: true,
          noiseSuppression: false,
          autoGainControl: true,
        },
      });
    expect(sources.captureReport.configured_microphone_gain).toBe(1.4);
    expect(sources.captureReport.configured_system_gain).toBe(0.9);
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
      settings: DEFAULT_CAPTURE_SETTINGS,
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
        settings: DEFAULT_CAPTURE_SETTINGS,
      }),
    ).rejects.toMatchObject({
      code: "display_cancelled",
      message: "Display capture was cancelled before the recording started.",
    } satisfies Partial<PickSourceError>);

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
        settings: DEFAULT_CAPTURE_SETTINGS,
      }),
    ).rejects.toMatchObject({
      code: "selected_microphone_unavailable",
      message:
        "The selected microphone is not available to the browser. Choose another microphone in Settings > Recording before starting the recording.",
    } satisfies Partial<PickSourceError>);

    expect(mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });
});
