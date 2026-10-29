import { describe, expect, it, vi } from "vitest";

import { pickCaptureSource } from "./pickSource";
import { readCaptureSettings, writeCaptureSettings } from "./shared";

const STORAGE_KEY = "nojoin.capture.settings";

const stored = () => {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  return raw === null ? null : (JSON.parse(raw) as Record<string, unknown>);
};

describe("capture processing defaults", () => {
  it("retains the chosen Start source when the microphone settings change", () => {
    writeCaptureSettings({ ...readCaptureSettings(), startMode: "microphone_only" });
    writeCaptureSettings({ ...readCaptureSettings(), microphoneDeviceId: "usb-mic", microphoneGain: 1.4 });

    expect(readCaptureSettings()).toMatchObject({
      startMode: "microphone_only", microphoneDeviceId: "usb-mic", microphoneGain: 1.4,
    });
  });

  it.each([
    { profile: "fresh", saved: null, expected: false },
    {
      profile: "unversioned with noise suppression enabled",
      saved: { microphoneGain: 1.4, noiseSuppression: true },
      expected: false,
    },
    {
      profile: "unversioned with noise suppression disabled",
      saved: { microphoneGain: 1.4, noiseSuppression: false },
      expected: false,
    },
    {
      profile: "unversioned without the noise suppression key",
      saved: { microphoneGain: 1.4 },
      expected: false,
    },
    {
      profile: "versioned with noise suppression enabled",
      saved: { microphoneGain: 1.4, noiseSuppression: true, version: 2 },
      expected: true,
    },
  ])("passes the $profile profile's processing to getUserMedia", async ({ saved, expected }) => {
    if (saved) {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
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
    expect(settings.microphoneGain).toBe(saved ? 1.4 : 1);
    sources.release();
  });
});

describe("noise suppression migration for settings saved before it defaulted off", () => {
  it("turns an unversioned stored true off once and saves the settings with the version", () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        microphoneDeviceId: "mic-1",
        microphoneGain: 1.4,
        systemGain: 0.8,
        echoCancellation: false,
        noiseSuppression: true,
        autoGainControl: true,
      }),
    );

    const settings = readCaptureSettings();

    expect(settings).toEqual({
      startMode: "shared_audio",
      microphoneDeviceId: "mic-1",
      microphoneGain: 1.4,
      systemGain: 0.8,
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: true,
    });
    expect(stored()).toEqual({ ...settings, version: 2 });
  });

  it("keeps an unversioned stored false", () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ microphoneGain: 1.4, noiseSuppression: false }),
    );

    expect(readCaptureSettings().noiseSuppression).toBe(false);
    expect(stored()).toMatchObject({ noiseSuppression: false, version: 2 });
  });

  it("keeps a versioned stored true without saving again", () => {
    const raw = JSON.stringify({ microphoneGain: 1.4, noiseSuppression: true, version: 2 });
    window.localStorage.setItem(STORAGE_KEY, raw);

    expect(readCaptureSettings().noiseSuppression).toBe(true);
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe(raw);
  });

  it("keeps noise suppression on once the user turns it back on after the migration", () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ noiseSuppression: true }));
    const migrated = readCaptureSettings();
    expect(migrated.noiseSuppression).toBe(false);

    writeCaptureSettings({ ...migrated, noiseSuppression: true });

    expect(readCaptureSettings().noiseSuppression).toBe(true);
    expect(readCaptureSettings().noiseSuppression).toBe(true);
  });

  it("gives the defaults and saves nothing when nothing was saved", () => {
    expect(readCaptureSettings()).toEqual({
      startMode: "shared_audio",
      microphoneDeviceId: null,
      microphoneGain: 1,
      systemGain: 1,
      echoCancellation: true,
      noiseSuppression: false,
      autoGainControl: true,
    });
    expect(stored()).toBeNull();
  });

  it("still returns the migrated settings when storage refuses the write", () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ microphoneGain: 1.4, noiseSuppression: true }),
    );
    const setItem = vi
      .spyOn(window.localStorage, "setItem")
      .mockImplementation(() => {
        throw new Error("QuotaExceededError");
      });

    const settings = readCaptureSettings();

    expect(settings.noiseSuppression).toBe(false);
    expect(settings.microphoneGain).toBe(1.4);
    setItem.mockRestore();
  });
});
