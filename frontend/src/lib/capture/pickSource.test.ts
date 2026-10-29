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

  describe("when the listing cannot vouch for the saved microphone", () => {
    // Listed before the page has microphone permission (Media Capture and
    // Streams, section 9.2.1).
    const placeholder = { kind: "audioinput", deviceId: "", label: "", groupId: "" };
    const listings = [
      { description: "blank-id placeholders", listed: () => Promise.resolve([placeholder]) },
      { description: "no microphones", listed: () => Promise.resolve([]) },
      { description: "a failed enumeration", listed: () => Promise.reject(new Error("not visible")) },
    ];
    const modes = ["shared_audio", "microphone_only"] as const;

    const build = (
      listed: () => Promise<unknown[]>,
      getUserMedia: ReturnType<typeof vi.fn>,
      shared = buildTrack("audio", "Shared"),
    ) =>
      ({
        enumerateDevices: vi.fn(listed),
        getDisplayMedia: vi.fn().mockResolvedValue(buildStream({ audioTracks: [shared] })),
        getUserMedia,
      }) as unknown as MediaDevices;

    it.each(listings.flatMap((listing) => modes.map((mode) => ({ ...listing, mode }))))(
      "starts with the exact id after $description in $mode",
      async ({ listed, mode }) => {
        const microphoneStream = buildStream({
          audioTracks: [buildTrack("audio", "USB Microphone", { deviceId: "mic-1" })],
        });
        const getUserMedia = vi.fn().mockResolvedValue(microphoneStream);

        const sources = await pickCaptureSource({
          mode,
          mediaDevices: build(listed, getUserMedia),
          microphoneDeviceId: "mic-1",
          settings: DEFAULT_CAPTURE_SETTINGS,
        });

        expect(sources.microphoneStream).toBe(microphoneStream);
        expect(getUserMedia).toHaveBeenCalledExactlyOnceWith({
          audio: expect.objectContaining({ deviceId: { exact: "mic-1" } }),
        });
        expect(sources.captureReport.notes).toEqual([]);
      },
    );

    it("keeps the placeholders in the source report", async () => {
      const sources = await pickCaptureSource({
        mode: "microphone_only",
        mediaDevices: build(
          () => Promise.resolve([placeholder]),
          vi.fn().mockResolvedValue(
            buildStream({ audioTracks: [buildTrack("audio", "USB Microphone", { deviceId: "mic-1" })] }),
          ),
        ),
        microphoneDeviceId: "mic-1",
        settings: DEFAULT_CAPTURE_SETTINGS,
      });

      expect(sources.captureReport.available_microphones).toEqual([
        { device_id: "", label: "Microphone 1" },
      ]);
      expect(sources.captureReport.requested_microphone_label).toBeNull();
    });

    it.each(["NotFoundError", "OverconstrainedError"])(
      "refuses once getUserMedia rejects the saved id with %s",
      async (name) => {
        const shared = buildTrack("audio", "Shared");
        const getUserMedia = vi.fn().mockRejectedValue(Object.assign(new Error(name), { name }));

        const failure = pickCaptureSource({
          mode: "shared_audio",
          mediaDevices: build(() => Promise.resolve([placeholder]), getUserMedia, shared),
          microphoneDeviceId: "mic-1",
          settings: DEFAULT_CAPTURE_SETTINGS,
        });

        await expect(failure).rejects.toMatchObject({
          code: "selected_microphone_unavailable",
          captureReport: expect.objectContaining({
            available_microphones: [{ device_id: "", label: "Microphone 1" }],
            notes: ["selected_microphone_rejected_by_getUserMedia"],
          }),
        } satisfies Partial<PickSourceError>);
        expect(shared.stop).toHaveBeenCalledOnce();
      },
    );

    it("still refuses up front when a listing with real ids lacks it", async () => {
      const getUserMedia = vi.fn();
      const mediaDevices = build(
        () => Promise.resolve([{ kind: "audioinput", deviceId: "mic-2", label: "Fallback Microphone" }]),
        getUserMedia,
      );

      await expect(
        pickCaptureSource({
          mode: "shared_audio",
          mediaDevices,
          microphoneDeviceId: "mic-1",
          settings: DEFAULT_CAPTURE_SETTINGS,
        }),
      ).rejects.toMatchObject({
        code: "selected_microphone_unavailable",
        captureReport: expect.objectContaining({
          notes: ["selected_microphone_missing_from_enumerated_devices"],
        }),
      } satisfies Partial<PickSourceError>);
      expect(mediaDevices.getDisplayMedia).not.toHaveBeenCalled();
      expect(getUserMedia).not.toHaveBeenCalled();
    });
  });
});
