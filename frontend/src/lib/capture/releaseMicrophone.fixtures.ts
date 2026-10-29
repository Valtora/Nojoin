import { expect, vi } from "vitest";

export const getUserMedia = vi.fn();
export const getDisplayMedia = vi.fn();
export const enumerateDevices = vi.fn();

export function installCaptureBrowser() {
  window.localStorage.clear();
  window.sessionStorage.clear();
  for (const storage of [window.localStorage, window.sessionStorage]) {
    storage.setItem("mic-test-probe", "works");
    expect(storage.getItem("mic-test-probe")).toBe("works");
    storage.removeItem("mic-test-probe");
  }
  const microphone = audioTrack();
  const sharedAudio = audioTrack("shared");
  const sharedVideo = audioTrack("screen", "video");
  const replacement = audioTrack();
  getUserMedia.mockReset().mockResolvedValueOnce(mediaStream(microphone))
    .mockResolvedValue(mediaStream(replacement));
  getDisplayMedia.mockReset().mockResolvedValue(mediaStream(sharedAudio, sharedVideo));
  enumerateDevices.mockReset().mockResolvedValue([microphoneDevice()]);
  const mediaDevices = { getUserMedia, getDisplayMedia, enumerateDevices };
  vi.stubGlobal("navigator", { mediaDevices });
  expect(navigator.mediaDevices).toBe(mediaDevices);
  return { microphone, sharedAudio, sharedVideo, replacement };
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

export function audioTrack(deviceId = "mic-a", kind = "audio") {
  let readyState = "live";
  const track = Object.assign(new EventTarget(), {
    kind,
    label: deviceId,
    enabled: true,
    muted: false,
    getSettings: () => ({ deviceId }),
    stop: vi.fn(() => { readyState = "ended"; }),
  });
  Object.defineProperty(track, "readyState", { get: () => readyState });
  return track as unknown as MediaStreamTrack;
}

export function mediaStream(...tracks: MediaStreamTrack[]): MediaStream {
  return {
    getTracks: () => tracks,
    getAudioTracks: () => tracks.filter((track) => track.kind === "audio"),
    getVideoTracks: () => tracks.filter((track) => track.kind === "video"),
  } as MediaStream;
}

export function microphoneDevice(deviceId = "mic-a") {
  return { kind: "audioinput", deviceId, label: deviceId, groupId: "group" } as MediaDeviceInfo;
}
