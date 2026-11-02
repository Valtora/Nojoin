/**
 * A browser that never starts audio (issue #924): Start and a Resume after a
 * reload give up after the mixer's time limit and leave nothing behind. Runs
 * the real controller, source picker and mixer over a fake audio graph.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureAudioGraph } from "./captureAudio.fixtures";
import { CaptureController } from "./controller";
import { AUDIO_START_TIMEOUT_MS, createCaptureMixer } from "./mixer";
import {
  audioTrack, getDisplayMedia, installCaptureBrowser, mediaStream,
} from "./releaseMicrophone.fixtures";
import { AudioNotStartedError } from "./shared";

const api = vi.hoisted(() => ({
  initRecording: vi.fn(), pauseRecordingCapture: vi.fn(), resumeRecordingCapture: vi.fn(),
  finalizeRecordingCapture: vi.fn(), discardRecordingCapture: vi.fn(),
  getRecording: vi.fn(), getUnfinishedCaptures: vi.fn(), getUserMe: vi.fn(),
  reportRecordingCaptureSources: vi.fn(),
}));
const environment = vi.hoisted(() => ({
  mode: "shared_audio",
  notify: vi.fn(),
  holdLock: vi.fn(() => () => {}),
  graph: null as unknown as CaptureAudioGraph,
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(), ...api,
}));
vi.mock("./featureDetect", () => ({
  detectCaptureSupport: () => ({ supported: true, mode: environment.mode }),
}));
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
  holdCaptureLock: environment.holdLock,
  isCaptureHeldInThisBrowser: async () => false,
}));
vi.mock("./mixer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./mixer")>();
  return {
    ...actual,
    createCaptureMixer: vi.fn((options: Parameters<typeof actual.createCaptureMixer>[0]) =>
      actual.createCaptureMixer({ ...options, ...environment.graph.mixerOptions() })),
  };
});
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

/** An audio context the browser never starts, as in headless Firefox. */
function audioThatNeverStarts() {
  const graph = new CaptureAudioGraph();
  graph.state = "suspended";
  graph.resume = () => new Promise<void>(() => {});
  return graph;
}

const controllers: CaptureController[] = [];
function controller() {
  const capture = new CaptureController();
  controllers.push(capture);
  return capture;
}

let microphone: MediaStreamTrack;
let sharedAudio: MediaStreamTrack;
let sharedVideo: MediaStreamTrack;

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  environment.mode = "shared_audio";
  ({ microphone, sharedAudio, sharedVideo } = installCaptureBrowser());
  api.initRecording.mockResolvedValue({ id: "rec-1", name: "Meeting", capture_epoch: 3 });
  api.pauseRecordingCapture.mockResolvedValue({
    recording_id: "rec-1", status: "PAUSED", last_sequence: 4, capture_epoch: 4,
  });
  api.resumeRecordingCapture.mockResolvedValue({
    recording_id: "rec-1", status: "UPLOADING", last_sequence: 4, capture_epoch: 4,
  });
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

describe("a browser that never starts audio (issue #924)", () => {
  it("fails Start cleanly after the time limit, so the next Start works", async () => {
    const graph = audioThatNeverStarts();
    environment.graph = graph;
    const capture = controller();

    const starting = capture.start("Meeting").catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(AUDIO_START_TIMEOUT_MS - 1);
    expect(createCaptureMixer).toHaveBeenCalledOnce();
    expect(capture.getState().status).toBe("starting");
    expect(microphone.stop).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);

    const failure = await starting;
    expect(failure).toBeInstanceOf(AudioNotStartedError);
    // The microphone and the share are released, and the context closed.
    expect(microphone.stop).toHaveBeenCalled();
    expect(sharedAudio.stop).toHaveBeenCalled();
    expect(sharedVideo.stop).toHaveBeenCalled();
    expect(graph.state).toBe("closed");
    // The server recording goes, so it cannot refuse the next Start.
    expect(api.discardRecordingCapture).toHaveBeenCalledWith(
      "rec-1", "audio_not_started", { epoch: 3 },
    );
    expect(environment.holdLock).not.toHaveBeenCalled();
    expect(capture.getState()).toMatchObject({
      status: "error",
      error: expect.stringMatching(/^The browser did not start audio, so the meeting was not started\./),
      recordingId: null,
      runtimeActive: false,
    });
    // A caller that reports the thrown error says the same.
    expect((failure as Error).message).toBe(capture.getState().error);

    environment.graph = new CaptureAudioGraph();
    getDisplayMedia.mockResolvedValue(mediaStream(audioTrack("shared"), audioTrack("screen", "video")));
    api.initRecording.mockResolvedValue({ id: "rec-2", name: "Meeting", capture_epoch: 1 });
    await expect(capture.start("Meeting")).resolves.toMatchObject({ recordingId: "rec-2" });
    expect(capture.getState()).toMatchObject({
      status: "recording", recordingId: "rec-2", runtimeActive: true,
    });
  });

  it("fails a microphone-only Start the same way", async () => {
    environment.mode = "microphone_only";
    environment.graph = audioThatNeverStarts();
    const capture = controller();

    const starting = capture.start("Meeting").catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(AUDIO_START_TIMEOUT_MS);

    expect(await starting).toBeInstanceOf(AudioNotStartedError);
    expect(getDisplayMedia).not.toHaveBeenCalled();
    expect(microphone.stop).toHaveBeenCalled();
    expect(api.discardRecordingCapture).toHaveBeenCalledWith(
      "rec-1", "audio_not_started", { epoch: 3 },
    );
    expect(capture.getState()).toMatchObject({ status: "error", recordingId: null });
  });

  it("leaves a recording paused when its Resume after a reload cannot start audio", async () => {
    environment.graph = audioThatNeverStarts();
    api.getUnfinishedCaptures.mockResolvedValue([{
      id: "rec-1", name: "Meeting", status: "PAUSED", client_status: "PAUSED", capture_epoch: 3,
    }]);
    const capture = controller();
    await capture.openRecording("rec-1");
    expect(capture.getState().pausedRecording?.id).toBe("rec-1");

    const resuming = capture.resume("rec-1").catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(AUDIO_START_TIMEOUT_MS);

    const failure = await resuming;
    expect(failure).toBeInstanceOf(AudioNotStartedError);
    expect(microphone.stop).toHaveBeenCalled();
    expect(sharedAudio.stop).toHaveBeenCalled();
    // Paused again under the epoch its Resume was given, and offered again.
    expect(api.pauseRecordingCapture).toHaveBeenCalledWith("rec-1", { epoch: 4 });
    expect(capture.getState()).toMatchObject({
      status: "error",
      error: expect.stringMatching(/^The browser did not start audio, so the recording is still paused\./),
      runtimeActive: false,
      pausedRecording: expect.objectContaining({ id: "rec-1" }),
    });
    expect((failure as Error).message).toBe(capture.getState().error);
  });
});
