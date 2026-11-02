import { waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CaptureController } from "./controller";
import { CaptureAudioGraph } from "./captureAudio.fixtures";
import {
  audioTrack, deferred, mediaStream, microphoneDevice, installCaptureBrowser,
  getUserMedia, getDisplayMedia, enumerateDevices,
} from "./releaseMicrophone.fixtures";
import { createCaptureMixer } from "./mixer";
import { createBrowserRecorder } from "./recorder";
import type { SegmentStore } from "./segmentStore";
import { CaptureResumeSupersededError, readPausedCaptureContext } from "./shared";
import { createSegmentUploader } from "./uploader";
import { createWaveformMonitor } from "./waveform";
const api = vi.hoisted(() => ({
  initRecording: vi.fn(), pauseRecordingCapture: vi.fn(), resumeRecordingCapture: vi.fn(),
  finalizeRecordingCapture: vi.fn(), discardRecordingCapture: vi.fn(),
  getRecording: vi.fn(), getUnfinishedCaptures: vi.fn(), getUserMe: vi.fn(),
  reportRecordingCaptureSources: vi.fn(),
}));
const environment = vi.hoisted(() => ({
  mode: "shared_audio", realDetection: false, notify: vi.fn(), releaseLock: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(), ...api,
}));
vi.mock("./featureDetect", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./featureDetect")>();
  return { detectCaptureSupport: () => environment.realDetection
    ? actual.detectCaptureSupport() : { supported: true, mode: environment.mode } };
});
vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: { getState: () => ({ addNotification: environment.notify }) },
}));
vi.mock("@/lib/session", () => ({
  holdSignInInPlace: vi.fn(), noteSignedIn: vi.fn(),
  retryAfterSignIn: (request: () => Promise<unknown>) => request(),
  useSessionStore: { getState: () => ({ heldInPlace: false, user: null }) },
}));
vi.mock("@/lib/connectivity/monitor", () => ({
  useConnectivityStore: {
    getState: () => ({ status: "online", lastReachableAt: null }),
    subscribe: () => () => {},
  },
}));
vi.mock("./captureLock", () => ({
  holdCaptureLock: () => environment.releaseLock,
  isCaptureHeldInThisBrowser: async () => false,
}));
vi.mock("./mixer", () => ({
  createCaptureMixer: vi.fn(async () => ({
    pcmSource: {}, systemAnalyser: {}, microphoneAnalyser: {}, mixedAnalyser: {},
    detachMicrophone: vi.fn(), replaceMicrophone: vi.fn(), resumeContext: vi.fn(async () => true),
    applySettings: vi.fn(), updateAutomaticGain: vi.fn(), dispose: vi.fn(async () => {}),
  })),
}));
vi.mock("./recorder", () => ({
  CAPTURE_SEGMENT_SECONDS: 2,
  createBrowserRecorder: vi.fn(() => ({
    start: vi.fn(), pause: vi.fn(async () => {}), resume: vi.fn(), stop: vi.fn(async () => {}),
  })),
}));
vi.mock("./uploader", () => ({
  createSegmentUploader: vi.fn(() => ({
    enqueue: vi.fn(), restore: vi.fn(), recover: vi.fn(), waitForIdle: vi.fn(async () => {}),
    dispose: vi.fn(), settled: vi.fn(async () => {}),
    pendingSegmentCount: vi.fn(() => 0), pendingAudioSeconds: vi.fn(() => 0),
  })),
}));
vi.mock("./waveform", () => ({
  createWaveformMonitor: vi.fn(() => ({ start: vi.fn(), stop: vi.fn() })),
}));
const lifecycle = (status: string, lastSequence = -1) => ({
  recording_id: "rec-1", status, last_sequence: lastSequence, capture_epoch: 3,
});
const paused = {
  id: "rec-1", name: "Meeting", status: "PAUSED", client_status: "PAUSED", capture_epoch: 3,
};
const controllers: CaptureController[] = [];
let microphone: MediaStreamTrack;
let sharedAudio: MediaStreamTrack;
let sharedVideo: MediaStreamTrack;
let replacement: MediaStreamTrack;
function controller() {
  const capture = new CaptureController();
  controllers.push(capture);
  return capture;
}
async function start(mode = "shared_audio", release = true) {
  environment.mode = mode;
  const capture = controller();
  capture.updateSettings({ releaseMicrophoneOnPause: release, microphoneDeviceId: "mic-a" });
  await capture.start("Meeting");
  return {
    capture,
    recorder: vi.mocked(createBrowserRecorder).mock.results.at(-1)!.value,
    uploader: vi.mocked(createSegmentUploader).mock.results.at(-1)!.value,
    mixer: await vi.mocked(createCaptureMixer).mock.results.at(-1)!.value,
    uploaderOptions: vi.mocked(createSegmentUploader).mock.calls.at(-1)![0],
  };
}
function storeOf(capture: CaptureController) {
  return (capture as unknown as { segmentStore: SegmentStore }).segmentStore;
}
beforeEach(() => {
  environment.realDetection = false;
  vi.clearAllMocks();
  vi.useRealTimers();
  ({ microphone, sharedAudio, sharedVideo, replacement } = installCaptureBrowser());
  api.initRecording.mockResolvedValue({ id: "rec-1", name: "Meeting", capture_epoch: 3 });
  api.pauseRecordingCapture.mockResolvedValue(lifecycle("PAUSED"));
  api.resumeRecordingCapture.mockResolvedValue(lifecycle("UPLOADING"));
  api.finalizeRecordingCapture.mockResolvedValue({ id: "rec-1", status: "QUEUED" });
  api.discardRecordingCapture.mockResolvedValue(undefined);
  api.getUnfinishedCaptures.mockResolvedValue([]);
  api.getRecording.mockResolvedValue({ id: "rec-1", status: "UPLOADING", captured_audio_seconds: 0 });
  api.getUserMe.mockResolvedValue({ id: 1 });
  api.reportRecordingCaptureSources.mockResolvedValue(undefined);
  vi.spyOn(console, "info").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
});
afterEach(async () => {
  await Promise.all(controllers.splice(0).map((capture) => capture.destroy()));
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
describe("releasing only the microphone while paused", () => {
  it("finishes the pause tail before releasing mic and retains shared tracks and ownership", async () => {
    const { capture, recorder, mixer, uploader } = await start();
    const flushed = deferred<void>();
    vi.mocked(recorder.pause).mockReturnValueOnce(flushed.promise);
    const pausing = capture.pause();
    expect(microphone.stop).not.toHaveBeenCalled();
    flushed.resolve();
    await pausing;
    expect(microphone.stop).toHaveBeenCalledTimes(1);
    expect(mixer.detachMicrophone).toHaveBeenCalledTimes(1);
    expect(sharedAudio.readyState).toBe("live");
    expect(sharedVideo.readyState).toBe("live");
    expect(mixer.dispose).not.toHaveBeenCalled();
    expect(uploader.dispose).not.toHaveBeenCalled();
    expect(environment.releaseLock).not.toHaveBeenCalled();
    expect(capture.getState()).toMatchObject({ status: "paused", runtimeActive: true });
  });
  it("reacquires the exact microphone before server resume and reuses recorder/uploader", async () => {
    const [realMixer, realRecorder, realWaveform, { encodeFlac }] = await Promise.all([
      vi.importActual<typeof import("./mixer")>("./mixer"),
      vi.importActual<typeof import("./recorder")>("./recorder"),
      vi.importActual<typeof import("./waveform")>("./waveform"),
      import("./pcm/flacEncoder"),
    ]);
    const graph = new CaptureAudioGraph();
    const encoded: Int16Array[] = [];
    let waveformFrame: FrameRequestCallback | undefined;
    vi.mocked(createCaptureMixer).mockImplementationOnce(async (options) => {
      const mixer = await realMixer.createCaptureMixer({ ...options, ...graph.mixerOptions() });
      vi.spyOn(mixer, "replaceMicrophone");
      return mixer;
    });
    vi.mocked(createBrowserRecorder).mockImplementationOnce((options) => {
      const recorder = realRecorder.createBrowserRecorder({
        ...options,
        encode: (pcm, ...format) => {
          encoded.push(pcm.slice());
          return encodeFlac(pcm, ...format);
        },
      });
      vi.spyOn(recorder, "resume");
      return recorder;
    });
    vi.mocked(createWaveformMonitor).mockImplementationOnce((options) =>
      realWaveform.createWaveformMonitor({
        ...options,
        requestAnimationFrameFn: (callback) => { waveformFrame = callback; return 1; },
        cancelAnimationFrameFn: () => {},
      }));
    const { capture, recorder, mixer, uploader } = await start();
    capture.updateSettings({ microphoneGain: 1.5 });
    const originalStream = await getUserMedia.mock.results[0].value;
    const sharedStream = await getDisplayMedia.mock.results[0].value;
    graph.signals.set(originalStream, -0.25);
    graph.signals.set(sharedStream, 0.0625);
    graph.render(128);
    await capture.pause();
    expect(microphone.readyState).toBe("ended");
    graph.render(4096 + 128);
    await capture.resume();
    const grantedStream = await getUserMedia.mock.results[1].value;
    expect(grantedStream).not.toBe(originalStream);
    expect(grantedStream.getAudioTracks()[0]).toBe(replacement);
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(getUserMedia.mock.calls[1][0]).toMatchObject({ audio: { deviceId: { exact: "mic-a" } } });
    expect(mixer.replaceMicrophone).toHaveBeenCalledOnce();
    expect(vi.mocked(mixer.replaceMicrophone).mock.calls[0][0]).toBe(grantedStream);
    expect(getUserMedia.mock.invocationCallOrder[1]).toBeLessThan(
      api.resumeRecordingCapture.mock.invocationCallOrder[0],
    );
    // The pause emitted the recorded audio as segment 0, so new audio starts at 1.
    expect(api.resumeRecordingCapture).toHaveBeenCalledWith("rec-1", { epoch: 3, nextSequence: 1 });
    expect(createBrowserRecorder).toHaveBeenCalledTimes(1);
    expect(createSegmentUploader).toHaveBeenCalledTimes(1);
    expect(getDisplayMedia).toHaveBeenCalledTimes(1);
    expect(recorder.resume).toHaveBeenCalledOnce();
    expect(capture.getState().status).toBe("recording");
    expect(readPausedCaptureContext()).toBeNull();
    graph.signals.set(grantedStream, 0.25);
    graph.render(4096);
    expect(waveformFrame).toBeTypeOf("function");
    waveformFrame!(0);
    expect(capture.getState().levels).toEqual({ system: 11, microphone: 45, mixed: 39 });
    await capture.stop();
    expect(encoded).toHaveLength(2);
    expect(Array.from(encoded[0])).toEqual(Array.from({ length: 128 }, () => [2048, -12288]).flat());
    expect(Array.from(encoded[1])).toEqual(Array.from({ length: 4096 }, () => [2048, 12288]).flat());
    expect(uploader.enqueue).toHaveBeenLastCalledWith(1, expect.any(Blob), 4096 / 16_000);
    expect(replacement.stop).toHaveBeenCalledOnce();
  });
  it.each([
    ["NotAllowedError", /permission|allow/i],
    ["NotReadableError", /busy|another app|in use/i],
    ["NotFoundError", /microphone|device/i],
  ])("keeps queue and paused state after %s and lets the next Resume retry", async (name, message) => {
    const { capture, recorder, uploader } = await start();
    await storeOf(capture).put({
      recordingId: "rec-1", sequence: 0, epoch: 3, blob: new Blob(["audio"]), durationSeconds: 1,
    });
    await capture.pause();
    getUserMedia.mockRejectedValueOnce(new DOMException("capture failed", name));
    await expect(capture.resume()).rejects.toThrow(message);
    expect(capture.getState()).toMatchObject({ status: "paused", runtimeActive: true });
    expect(capture.getState().error).toMatch(message);
    expect(await storeOf(capture).list("rec-1")).toHaveLength(1);
    expect(recorder.resume).not.toHaveBeenCalled();
    expect(uploader.dispose).not.toHaveBeenCalled();
    await expect(capture.resume()).resolves.toMatchObject({ recordingId: "rec-1", resumed: true });
    expect(recorder.resume).toHaveBeenCalledOnce();
  });
  it.each(["stop", "cancel"] as const)("%s supersedes a pending audio resume boundary", async (action) => {
    const { capture, recorder } = await start();
    await capture.pause();
    const boundary = deferred<void>();
    vi.mocked(recorder.resume).mockReturnValueOnce(boundary.promise);
    const resuming = capture.resume().catch((error: unknown) => error);
    await waitFor(() => expect(recorder.resume).toHaveBeenCalledOnce());
    expect(capture.getState().status).toBe("paused");
    expect(readPausedCaptureContext()).not.toBeNull();
    await capture[action]();
    boundary.resolve();
    expect(await resuming).toBeInstanceOf(CaptureResumeSupersededError);
    expect(capture.getState()).toMatchObject({ status: "idle", runtimeActive: false, error: null });
  });
  it("falls back with a warning only when enumeration proves the selected mic absent", async () => {
    const { capture } = await start();
    await capture.pause();
    enumerateDevices.mockResolvedValue([microphoneDevice("other")]);
    replacement = audioTrack("default-device");
    getUserMedia.mockResolvedValue(mediaStream(replacement));
    await capture.resume();
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(getUserMedia.mock.calls[1][0].audio.deviceId).toBeUndefined();
    expect(environment.notify).toHaveBeenCalledWith(expect.objectContaining({
      type: "warning",
      message: "The selected microphone is not available, so Nojoin is recording from the system default microphone.",
    }));
  });
  it("keeps requesting the selected mic when enumeration fails", async () => {
    const { capture } = await start();
    await capture.pause();
    enumerateDevices.mockRejectedValue(new Error("enumeration unavailable"));
    await capture.resume();
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(getUserMedia.mock.calls[1][0]).toMatchObject({ audio: { deviceId: { exact: "mic-a" } } });
    expect(environment.notify).not.toHaveBeenCalledWith(expect.objectContaining({
      message: expect.stringMatching(/default/i),
    }));
  });
  it("keeps following the system default when no microphone was chosen", async () => {
    environment.mode = "microphone_only";
    const capture = controller();
    capture.updateSettings({ releaseMicrophoneOnPause: true, microphoneDeviceId: null });
    await capture.start("Meeting");
    await capture.pause();
    // The default track reports a concrete device; Resume must not pin it.
    expect(microphone.getSettings().deviceId).toBe("mic-a");
    await capture.resume();
    expect(getUserMedia.mock.calls[1][0].audio.deviceId).toBeUndefined();
    expect(capture.getState().status).toBe("recording");
  });
  it.each(["stop", "cancel"] as const)("%s while released never asks for another microphone", async (action) => {
    const { capture } = await start();
    await capture.pause();
    await capture[action]();
    expect(microphone.stop).toHaveBeenCalled();
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(sharedAudio.stop).toHaveBeenCalledOnce();
    expect(sharedVideo.stop).toHaveBeenCalledOnce();
    expect(capture.getState()).toMatchObject({ status: "idle", runtimeActive: false });
  });
  it.each(["stop", "cancel"] as const)("%s supersedes pending mic acquisition", async (action) => {
    const { capture, recorder } = await start();
    await capture.pause();
    const grant = deferred<MediaStream>();
    getUserMedia.mockReturnValueOnce(grant.promise);
    const resuming = capture.resume().catch((error: unknown) => error);
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(2));
    await capture[action]();
    grant.resolve(mediaStream(replacement));
    expect(await resuming).toBeInstanceOf(CaptureResumeSupersededError);
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(replacement.stop).toHaveBeenCalledOnce();
    expect(recorder.resume).not.toHaveBeenCalled();
    expect(capture.getState()).toMatchObject({ status: "idle", runtimeActive: false, error: null });
  });
  it.each(["stop", "cancel"] as const)("%s during the audio context wait stands the Resume aside", async (action) => {
    const { capture, mixer } = await start();
    await capture.pause();
    const rendering = deferred<boolean>();
    vi.mocked(mixer.resumeContext).mockReturnValueOnce(rendering.promise);
    const resuming = capture.resume().catch((error: unknown) => error);
    await waitFor(() => expect(mixer.resumeContext).toHaveBeenCalledOnce());
    await capture[action]();
    // Closing the context on teardown leaves it not running.
    rendering.resolve(false);
    expect(await resuming).toBeInstanceOf(CaptureResumeSupersededError);
    expect(api.resumeRecordingCapture).not.toHaveBeenCalled();
    expect(replacement.readyState).toBe("ended");
    expect(capture.getState()).toMatchObject({ status: "idle", runtimeActive: false, error: null });
  });
  it.each([
    ["stop", "accepts"], ["stop", "refuses"], ["cancel", "accepts"], ["cancel", "refuses"],
  ] as const)("%s during a slow server Resume with the setting off stands it aside when the server %s", async (action, answer) => {
    const { capture } = await start("shared_audio", false);
    await capture.pause();
    const server = deferred<unknown>();
    api.resumeRecordingCapture.mockReturnValueOnce(server.promise);
    const resuming = capture.resume().catch((error: unknown) => error);
    await waitFor(() => expect(api.resumeRecordingCapture).toHaveBeenCalledOnce());
    await capture[action]();
    if (answer === "accepts") {
      server.resolve(lifecycle("UPLOADING"));
    } else {
      // What the server says once the recording has been stopped.
      server.reject(Object.assign(new Error("Request failed with status code 409"), {
        response: { status: 409, data: { detail: "Recording is no longer accepting capture uploads" } },
      }));
    }
    expect(await resuming).toBeInstanceOf(CaptureResumeSupersededError);
    expect(capture.getState()).toMatchObject({ status: "idle", runtimeActive: false, error: null });
  });

  it.each([false, true])("entering the recording page preserves pending Resume, cold=%s", async (cold) => {
    const { capture } = await start("microphone_only");
    await capture.pause();
    let resumingCapture = capture;
    if (cold) {
      await capture.destroy();
      resumingCapture = controller();
      api.getUnfinishedCaptures.mockResolvedValue([paused]);
      await resumingCapture.openRecording("rec-1");
    }
    const grant = deferred<MediaStream>();
    getUserMedia.mockReturnValueOnce(grant.promise);
    const resuming = resumingCapture.resume("rec-1");
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(2));

    await resumingCapture.openRecording("rec-1");
    grant.resolve(mediaStream(replacement));
    await expect(resuming).resolves.toMatchObject({ recordingId: "rec-1", resumed: true });
    expect(resumingCapture.getState()).toMatchObject({ status: "recording", runtimeActive: true });
    expect(replacement.readyState).toBe("live");
  });
  it("releases the microphone when an upload stall pauses capture", async () => {
    const { capture, uploaderOptions } = await start();
    await uploaderOptions.onStalled?.(new Error("upload stalled"));
    expect(microphone.stop).toHaveBeenCalledOnce();
    expect(sharedAudio.readyState).toBe("live");
    expect(capture.getState()).toMatchObject({ status: "paused", runtimeActive: true });
  });
  it("supports microphone-only pause/resume without a shared picker", async () => {
    const { capture } = await start("microphone_only");
    await capture.pause();
    expect(microphone.stop).toHaveBeenCalledOnce();
    await capture.resume();
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(getDisplayMedia).not.toHaveBeenCalled();
    await capture.cancel();
    expect(replacement.stop).toHaveBeenCalledOnce();
  });
  it("keeps microphone and graph unchanged with the setting off", async () => {
    const { capture, mixer } = await start("shared_audio", false);
    await capture.pause();
    expect(microphone.readyState).toBe("live");
    expect(mixer.detachMicrophone).not.toHaveBeenCalled();
    await capture.resume();
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(mixer.replaceMicrophone).not.toHaveBeenCalled();
    // A context the browser suspended during the pause renders again.
    expect(mixer.resumeContext).toHaveBeenCalledOnce();
    expect(capture.getState().status).toBe("recording");
  });
  it.each([false, true])("stays paused with a reason while the audio context stays interrupted, release=%s", async (release) => {
    const { capture, recorder, mixer } = await start("shared_audio", release);
    await capture.pause();
    vi.mocked(mixer.resumeContext).mockResolvedValueOnce(false);
    await expect(capture.resume()).rejects.toThrow(/has not restarted audio capture/);
    expect(api.resumeRecordingCapture).not.toHaveBeenCalled();
    expect(recorder.resume).not.toHaveBeenCalled();
    expect(capture.getState()).toMatchObject({ status: "paused", runtimeActive: true });
    expect(capture.getState().error).toMatch(/has not restarted audio capture/);
    // A reopened microphone is released again, so nothing stays live while paused.
    expect(replacement.readyState).toBe(release ? "ended" : "live");
    getUserMedia.mockResolvedValueOnce(mediaStream(audioTrack()));
    await expect(capture.resume()).resolves.toMatchObject({ recordingId: "rec-1", resumed: true });
    expect(capture.getState().status).toBe("recording");
  });
  it("reacquires a released mic after the release setting is disabled", async () => {
    const { capture } = await start();
    await capture.pause();
    capture.updateSettings({ releaseMicrophoneOnPause: false });
    await capture.resume();
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(capture.getState().status).toBe("recording");
  });

  it("honours a deliberate new microphone choice after release", async () => {
    const { capture } = await start("microphone_only");
    await capture.pause();
    capture.updateSettings({ microphoneDeviceId: "mic-b" });
    enumerateDevices.mockResolvedValue([microphoneDevice(), microphoneDevice("mic-b")]);
    getUserMedia.mockResolvedValue(mediaStream(audioTrack("mic-b")));
    await capture.resume("rec-1");
    expect(getUserMedia.mock.calls.at(-1)![0]).toMatchObject({ audio: { deviceId: { exact: "mic-b" } } });
    expect(api.reportRecordingCaptureSources.mock.calls.at(-1)![1].requested_microphone_label).toBeNull();
    expect(capture.getState().status).toBe("recording");
  });

  it("releases an acquired mic if server Resume fails and reacquires on retry", async () => {
    const { capture, recorder } = await start();
    await capture.pause();
    api.resumeRecordingCapture.mockRejectedValueOnce(new Error("server unavailable"));
    await expect(capture.resume()).rejects.toThrow("server unavailable");
    expect(replacement.stop).toHaveBeenCalledOnce();
    expect(capture.getState().status).toBe("paused");
    expect(recorder.resume).not.toHaveBeenCalled();
    const retriedTrack = audioTrack();
    getUserMedia.mockResolvedValueOnce(mediaStream(retriedTrack));
    await capture.resume();
    expect(getUserMedia).toHaveBeenCalledTimes(3);
    expect(retriedTrack.readyState).toBe("live");
    expect(recorder.resume).toHaveBeenCalledOnce();
  });

  it("shares one Resume between concurrent clicks, with one microphone request and no error", async () => {
    const { capture, recorder } = await start();
    await capture.pause();
    const grant = deferred<MediaStream>();
    getUserMedia.mockReturnValueOnce(grant.promise);
    const first = capture.resume();
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(2));
    const second = capture.resume();
    grant.resolve(mediaStream(replacement));
    await expect(first).resolves.toMatchObject({ recordingId: "rec-1", resumed: true });
    await expect(second).resolves.toMatchObject({ recordingId: "rec-1", resumed: true });
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(api.resumeRecordingCapture).toHaveBeenCalledOnce();
    expect(recorder.resume).toHaveBeenCalledOnce();
    expect(capture.getState().status).toBe("recording");
  });

  it.each(["Android Chrome/140.0 Mobile Safari/537.36", "iPhone CriOS/140.0 Mobile Safari/604.1"])(
    "uses actual mobile support detection for %s", async (userAgent) => {
      environment.realDetection = true;
      vi.stubGlobal("navigator", { userAgent, mediaDevices: navigator.mediaDevices });
      vi.stubGlobal("AudioWorkletNode", class {});
      expect(navigator.userAgent).toBe(userAgent);
      expect(typeof AudioWorkletNode).toBe("function");
      const { capture } = await start();
      expect(capture.getState().support).toEqual({ supported: true, mode: "microphone_only" });
      await capture.pause();
      await capture.resume();
      expect(microphone.readyState).toBe("ended");
      expect(getUserMedia).toHaveBeenCalledTimes(2);
      expect(getDisplayMedia).not.toHaveBeenCalled();
    },
  );
});
