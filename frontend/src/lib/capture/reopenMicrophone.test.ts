// @vitest-environment node

import { describe, expect, it, vi } from "vitest";

import { DEFAULT_CAPTURE_SETTINGS } from "./shared";

const track = (deviceId = "mic-1", readyState = "live") => ({
  kind: "audio",
  readyState,
  getSettings: () => ({ deviceId }),
  stop: vi.fn(),
});

const streamWith = (tracks = [track()]) => ({
  getAudioTracks: () => tracks,
  getTracks: () => tracks,
}) as unknown as MediaStream;

const setup = (deviceIds = ["mic-1"]) => {
  const stream = streamWith();
  const mediaDevices = {
    enumerateDevices: vi.fn().mockResolvedValue(
      deviceIds.map((deviceId) => ({ kind: "audioinput", deviceId })),
    ),
    getUserMedia: vi.fn().mockResolvedValue(stream),
  };
  const reopen = async (microphoneDeviceId: string | null = "mic-1") => {
    const { reopenMicrophone } = await import("./reopenMicrophone");
    return reopenMicrophone({
      microphoneDeviceId,
      settings: DEFAULT_CAPTURE_SETTINGS,
      mediaDevices: mediaDevices as unknown as MediaDevices,
    });
  };
  return { mediaDevices, reopen, stream };
};

describe("reopening a released microphone", () => {
  it("requests the same listed device and browser processing settings", async () => {
    const { mediaDevices, reopen, stream } = setup();
    await expect(reopen()).resolves.toEqual({ stream, usedDefault: false });
    expect(mediaDevices.getUserMedia).toHaveBeenCalledExactlyOnceWith({
      audio: {
        deviceId: { exact: "mic-1" },
        echoCancellation: true,
        noiseSuppression: false,
        autoGainControl: true,
      },
    });
  });

  it.each([
    { description: "another microphone", deviceIds: ["mic-2"] },
    { description: "no microphones", deviceIds: [] },
  ])("uses default only when enumeration confirms absence: $description", async ({ deviceIds }) => {
    const { mediaDevices, reopen, stream } = setup(deviceIds);
    await expect(reopen()).resolves.toEqual({ stream, usedDefault: true });
    expect(mediaDevices.getUserMedia.mock.calls[0][0].audio.deviceId).toBeUndefined();
  });

  it("keeps requesting the selected device when enumeration fails", async () => {
    const { mediaDevices, reopen } = setup();
    mediaDevices.enumerateDevices.mockRejectedValue(new Error("not visible"));
    await reopen();
    expect(mediaDevices.getUserMedia.mock.calls[0][0].audio.deviceId).toEqual({ exact: "mic-1" });
  });

  it("uses the browser default without claiming a fallback when no device was selected", async () => {
    const { mediaDevices, reopen, stream } = setup();
    await expect(reopen(null)).resolves.toEqual({ stream, usedDefault: false });
    expect(mediaDevices.getUserMedia.mock.calls[0][0].audio.deviceId).toBeUndefined();
  });

  it.each([
    ["NotAllowedError", /permission.*allow.*resume/i],
    ["NotReadableError", /busy.*close.*resume/i],
    ["NotFoundError", /unavailable.*connect.*resume/i],
    ["OverconstrainedError", /unavailable.*connect.*resume/i],
  ])("keeps %s failures actionable and never retries a default input", async (name, message) => {
    const { mediaDevices, reopen } = setup();
    mediaDevices.getUserMedia.mockRejectedValue(Object.assign(new Error(name), { name }));
    await expect(reopen()).rejects.toThrow(message);
    expect(mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
  });

  it.each(["ended", "missing"])('stops an invalid "%s" microphone grant', async (state) => {
    const { mediaDevices, reopen } = setup();
    const invalidTrack = track("mic-1", "ended");
    const strayTrack = track();
    const invalidStream = {
      getAudioTracks: () => state === "missing" ? [] : [invalidTrack],
      getTracks: () => [invalidTrack, strayTrack],
    } as unknown as MediaStream;
    mediaDevices.getUserMedia.mockResolvedValue(invalidStream);
    await expect(reopen()).rejects.toThrow(/unavailable.*resume/i);
    expect(invalidTrack.stop).toHaveBeenCalledOnce();
    expect(strayTrack.stop).toHaveBeenCalledOnce();
  });

  it("stops an unexpected device instead of accepting a mismatched exact grant", async () => {
    const { mediaDevices, reopen } = setup();
    const wrongTrack = track("mic-2");
    mediaDevices.getUserMedia.mockResolvedValue(streamWith([wrongTrack]));
    await expect(reopen()).rejects.toThrow(/selected microphone.*settings.*resume/i);
    expect(wrongTrack.stop).toHaveBeenCalledOnce();
  });
});
