import { describe, expect, it, vi } from "vitest";

import { pickCaptureSource } from "./pickSource";
import { readCaptureSettings } from "./shared";

describe("capture processing defaults", () => {
  it.each([
    { profile: "fresh", stored: null, expected: false },
    {
      profile: "stored with noise suppression enabled",
      stored: { microphoneGain: 1.4, noiseSuppression: true },
      expected: true,
    },
    {
      profile: "stored with noise suppression disabled",
      stored: { microphoneGain: 1.4, noiseSuppression: false },
      expected: false,
    },
    {
      profile: "stored without the noise suppression key",
      stored: { microphoneGain: 1.4 },
      expected: false,
    },
  ])("passes the $profile profile's processing to getUserMedia", async ({ stored, expected }) => {
    if (stored) {
      window.localStorage.setItem("nojoin.capture.settings", JSON.stringify(stored));
    }
    const settings = readCaptureSettings();
    const microphoneStream = {
      getTracks: () => [],
      getAudioTracks: () => [],
    } as unknown as MediaStream;
    const getUserMedia = vi.fn().mockResolvedValue(microphoneStream);
    const mediaDevices = {
      getUserMedia,
      enumerateDevices: vi.fn().mockResolvedValue([]),
    } as unknown as MediaDevices;

    const sources = await pickCaptureSource({
      mode: "microphone_only",
      settings,
      mediaDevices,
    });

    expect(getUserMedia).toHaveBeenCalledWith({
      audio: {
        deviceId: undefined,
        echoCancellation: true,
        noiseSuppression: expected,
        autoGainControl: true,
      },
    });
    expect(settings.noiseSuppression).toBe(expected);
    expect(settings.microphoneGain).toBe(stored ? 1.4 : 1);
    sources.release();
  });
});
