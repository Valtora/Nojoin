import { AxiosError } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  noteSessionRefusal,
  noteSignedIn,
  resetSessionForTests,
  useSessionStore,
} from "@/lib/session";

import { CaptureController } from "./controller";
import { LEFT_OUT_AUDIO_KEEP_MS, type LeftOutAudio, type LeftOutAudioStore } from "./leftOutAudio";
import { sendPauseBeacon } from "./lifecycle";
import { encodeFlac } from "./pcm/flacEncoder";
import { decodeFlac } from "./pcm/testFlacDecoder";
import { PickSourceError } from "./pickSource";
import { createBrowserRecorder } from "./recorder";
import type { SegmentStore } from "./segmentStore";
import { createSegmentUploader } from "./uploader";

const apiMocks = vi.hoisted(() => ({
  /** As in @/lib/api: a 409 whose detail says the capture continued elsewhere. */
  isCaptureSupersededError: (error: unknown) => {
    const response = (
      error as { response?: { status?: number; data?: { detail?: { code?: string } } } } | null
    )?.response;
    return response?.status === 409 && response.data?.detail?.code === "capture_superseded";
  },
  /** As in @/lib/api: a 409 saying the recording was stopped and takes no more audio. */
  isCaptureClosedError: (error: unknown) => {
    const response = (
      error as { response?: { status?: number; data?: { detail?: unknown } } } | null
    )?.response;
    return (
      response?.status === 409 &&
      response.data?.detail === "Recording is no longer accepting capture uploads"
    );
  },
  discardRecordingCapture: vi.fn(),
  getRecording: vi.fn(),
  finalizeRecordingCapture: vi.fn(),
  pauseRecordingCapture: vi.fn(),
  getUnfinishedCaptures: vi.fn(),
  getUserMe: vi.fn(),
  initRecording: vi.fn(),
  reportRecordingCaptureSources: vi.fn(),
  resumeRecordingCapture: vi.fn(),
}));

const featureDetectMocks = vi.hoisted(() => ({
  detectCaptureSupport: vi.fn(() => ({ supported: true, mode: "shared_audio" })),
}));

const pickSourceMocks = vi.hoisted(() => ({
  pickCaptureSource: vi.fn(),
}));

// Mutable so a test can put the backend "down" without rebuilding the store.
const connectivityMocks = vi.hoisted(() => {
  type Listener = (state: { status: string; lastReachableAt: number | null }) => void;
  const mocks = {
    status: "online" as string,
    lastReachableAt: null as number | null,
    listeners: new Set<Listener>(),
    /**
     * The monitor seeing the API answer, as a probe or any request does. Later
     * than anything else this millisecond, as a real answer would be.
     */
    apiAnswered: (at = Date.now() + 1) => {
      mocks.lastReachableAt = at;
      const state = { status: mocks.status, lastReachableAt: at };
      mocks.listeners.forEach((listener) => listener(state));
    },
  };
  return mocks;
});

vi.mock("@/lib/api", () => ({
  discardRecordingCapture: apiMocks.discardRecordingCapture,
  finalizeRecordingCapture: apiMocks.finalizeRecordingCapture,
  getUnfinishedCaptures: apiMocks.getUnfinishedCaptures,
  getRecording: apiMocks.getRecording,
  getUserMe: apiMocks.getUserMe,
  initRecording: apiMocks.initRecording,
  isActiveRecordingConflictDetail: vi.fn(() => false),
  isCaptureClosedError: apiMocks.isCaptureClosedError,
  isCaptureEndedError: (error: unknown) =>
    apiMocks.isCaptureSupersededError(error) || apiMocks.isCaptureClosedError(error),
  isCaptureSupersededError: apiMocks.isCaptureSupersededError,
  pauseRecordingCapture: apiMocks.pauseRecordingCapture,
  reportRecordingCaptureSources: apiMocks.reportRecordingCaptureSources,
  resumeRecordingCapture: apiMocks.resumeRecordingCapture,
}));

const captureLockMocks = vi.hoisted(() => {
  const release = vi.fn();
  return {
    release,
    holdCaptureLock: vi.fn(() => release),
    isCaptureHeldInThisBrowser: vi.fn(async () => false),
  };
});

vi.mock("./captureLock", () => ({
  holdCaptureLock: captureLockMocks.holdCaptureLock,
  isCaptureHeldInThisBrowser: captureLockMocks.isCaptureHeldInThisBrowser,
}));

vi.mock("@/lib/connectivity/monitor", () => ({
  useConnectivityStore: {
    getState: () => ({
      status: connectivityMocks.status,
      lastReachableAt: connectivityMocks.lastReachableAt,
    }),
    subscribe: (
      listener: (state: { status: string; lastReachableAt: number | null }) => void,
    ) => {
      connectivityMocks.listeners.add(listener);
      return () => {
        connectivityMocks.listeners.delete(listener);
      };
    },
  },
}));

vi.mock("./featureDetect", () => ({
  detectCaptureSupport: featureDetectMocks.detectCaptureSupport,
}));

vi.mock("./pickSource", () => ({
  PickSourceError: class PickSourceError extends Error {
    code: string;

    constructor(code: string, message: string) {
      super(message);
      this.code = code;
      this.name = "PickSourceError";
    }
  },
  pickCaptureSource: pickSourceMocks.pickCaptureSource,
}));

vi.mock("./lifecycle", () => ({
  CaptureLifecycle: class {
    attach() {}
    detach() {}
    updateRecordingId() {}
    updateRouteSignature() {}
    resetGuard() {}
  },
  sendPauseBeacon: vi.fn(() => false),
}));

const sharedMocks = vi.hoisted(() => ({
  readPausedCaptureContext: vi.fn(() => null as unknown),
}));

vi.mock("./shared", () => ({
  clearPausedCaptureContext: vi.fn(),
  DEFAULT_CAPTURE_LEVELS: { system: 0, microphone: 0, mixed: 0 },
  readCaptureSettings: vi.fn(() => ({
    microphoneDeviceId: null,
    microphoneGain: 1,
    systemGain: 1,
    echoCancellation: true,
    noiseSuppression: false,
    autoGainControl: true,
  })),
  readPausedCaptureContext: sharedMocks.readPausedCaptureContext,
  writeCaptureSettings: vi.fn(),
  writePausedCaptureContext: vi.fn(),
}));

const notificationMocks = vi.hoisted(() => ({
  addNotification: vi.fn(),
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: {
    getState: () => ({
      addNotification: notificationMocks.addNotification,
    }),
  },
}));

vi.mock("./mixer", () => ({
  createCaptureMixer: vi.fn(() => Promise.resolve({
    pcmSource: {},
    systemAnalyser: {},
    microphoneAnalyser: {},
    mixedAnalyser: {},
    updateAutomaticGain: vi.fn(),
    dispose: vi.fn(),
  })),
}));

vi.mock("./recorder", () => ({
  CAPTURE_SEGMENT_SECONDS: 2,
  createBrowserRecorder: vi.fn(() => ({
    start: vi.fn(),
    // Resolves, as the recorder's own stop does.
    stop: vi.fn(async () => {}),
    pause: vi.fn(),
    resume: vi.fn(),
  })),
}));

vi.mock("./uploader", () => ({
  createSegmentUploader: vi.fn(() => ({
    enqueue: vi.fn(),
    restore: vi.fn(),
    waitForIdle: vi.fn(),
    recover: vi.fn(() => true),
    dispose: vi.fn(),
    settled: vi.fn(async () => {}),
    pendingSegmentCount: vi.fn(() => 0),
    pendingAudioSeconds: vi.fn(() => 0),
  })),
}));

vi.mock("./waveform", () => ({
  createWaveformMonitor: vi.fn(() => ({
    start: vi.fn(),
    stop: vi.fn(),
  })),
}));

const screenStudyMocks = vi.hoisted(() => {
  const session = {
    recordingId: "rec-study",
    tick: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    stopSampling: vi.fn(),
    audioQueueChanged: vi.fn(),
    finish: vi.fn(async () => {}),
    abort: vi.fn(),
    deleteFrames: vi.fn(async () => {}),
  };
  return { session, startScreenStudySession: vi.fn(() => session) };
});

vi.mock("./screenStudy/session", () => ({
  startScreenStudySession: screenStudyMocks.startScreenStudySession,
}));

const buildConflictError = (detail: string) => {
  const error = new AxiosError("Request failed with status code 409");
  Object.assign(error, {
    response: {
      status: 409,
      data: { detail },
    },
  });
  return error;
};

describe("capture controller", () => {
  beforeEach(() => {
    apiMocks.discardRecordingCapture.mockReset();
    apiMocks.discardRecordingCapture.mockResolvedValue(undefined);
    apiMocks.finalizeRecordingCapture.mockReset();
    apiMocks.pauseRecordingCapture.mockReset();
    apiMocks.getUnfinishedCaptures.mockReset();
    apiMocks.initRecording.mockReset();
    apiMocks.reportRecordingCaptureSources.mockReset();
    apiMocks.reportRecordingCaptureSources.mockResolvedValue(undefined);
    apiMocks.resumeRecordingCapture.mockReset();
    apiMocks.resumeRecordingCapture.mockResolvedValue({
      recording_id: "rec-1",
      status: "UPLOADING",
      last_sequence: 0,
    });
    apiMocks.getUnfinishedCaptures.mockResolvedValue([]);
    featureDetectMocks.detectCaptureSupport.mockReset();
    featureDetectMocks.detectCaptureSupport.mockReturnValue({
      supported: true,
      mode: "shared_audio",
    });
    pickSourceMocks.pickCaptureSource.mockReset();
    notificationMocks.addNotification.mockReset();
    apiMocks.getRecording.mockReset();
    apiMocks.getRecording.mockResolvedValue({ id: "rec-1" });
    sharedMocks.readPausedCaptureContext.mockReset();
    sharedMocks.readPausedCaptureContext.mockReturnValue(null);
    connectivityMocks.status = "online";
    connectivityMocks.lastReachableAt = null;
    connectivityMocks.listeners.clear();
    captureLockMocks.release.mockClear();
    captureLockMocks.holdCaptureLock.mockClear();
    captureLockMocks.isCaptureHeldInThisBrowser.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("retries finalize until the upload settles", async () => {
    vi.useFakeTimers();

    apiMocks.finalizeRecordingCapture
      .mockRejectedValueOnce(
        buildConflictError(
          "Recording upload is still in progress; finalize after all segment uploads complete.",
        ),
      )
      .mockRejectedValueOnce(
        buildConflictError(
          "Recording upload is still in progress; finalize after all segment uploads complete.",
        ),
      )
      .mockRejectedValueOnce(
        buildConflictError(
          "Recording upload is still in progress; finalize after all segment uploads complete.",
        ),
      )
      .mockRejectedValueOnce(
        buildConflictError(
          "Recording upload is still in progress; finalize after all segment uploads complete.",
        ),
      )
      .mockRejectedValueOnce(
        buildConflictError(
          "Recording upload is still in progress; finalize after all segment uploads complete.",
        ),
      )
      .mockRejectedValueOnce(
        buildConflictError(
          "Recording upload is still in progress; finalize after all segment uploads complete.",
        ),
      )
      .mockRejectedValueOnce(
        buildConflictError(
          "Recording upload is still in progress; finalize after all segment uploads complete.",
        ),
      )
      .mockResolvedValue({ id: "rec-1", status: "QUEUED" });

    const controller = new CaptureController();
    const finalizePromise = (controller as any).finalizeRecordingWhenReady("rec-1");

    await vi.runAllTimersAsync();

    await expect(finalizePromise).resolves.toEqual({ id: "rec-1", status: "QUEUED" });
    expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalledTimes(8);
  });

  it("exposes finalize retry progress and clears it when finalize settles", async () => {
    vi.useFakeTimers();

    const detail =
      "Recording upload is still in progress; finalize after all segment uploads complete.";
    apiMocks.finalizeRecordingCapture
      .mockRejectedValueOnce(buildConflictError(detail))
      .mockRejectedValueOnce(buildConflictError(detail))
      .mockResolvedValue({ id: "rec-1", status: "QUEUED" });

    const controller = new CaptureController();
    const seenRetries: Array<{ attempt: number; maxAttempts: number } | null> = [];
    controller.subscribe((state) => seenRetries.push(state.finalizeRetry));

    const finalizePromise = (controller as any).finalizeRecordingWhenReady("rec-1");
    await vi.runAllTimersAsync();

    await expect(finalizePromise).resolves.toEqual({ id: "rec-1", status: "QUEUED" });
    expect(seenRetries).toContainEqual({ attempt: 1, maxAttempts: 11 });
    expect(seenRetries).toContainEqual({ attempt: 2, maxAttempts: 11 });
    expect(controller.getState().finalizeRetry).toBeNull();
  });

  it("releases capture media as soon as uploads drain, before finalize completes", async () => {
    const sources = {
      mode: "microphone_only",
      displayStream: null,
      microphoneStream: {} as MediaStream,
      captureReport: {
        mode: "microphone_only",
        requested_microphone_device_id: null,
        requested_microphone_label: null,
        available_microphones: [],
        browser_microphone_track: null,
        browser_display_audio_track: null,
        browser_display_video_track: null,
        shared_audio_available: false,
        notes: [],
      },
      release: vi.fn(),
    };

    pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);
    apiMocks.initRecording.mockResolvedValue({ id: "rec-1", name: "Test meeting" });

    let resolveFinalize: (value: unknown) => void = () => {};
    apiMocks.finalizeRecordingCapture.mockReturnValue(
      new Promise((resolve) => {
        resolveFinalize = resolve;
      }),
    );

    const controller = new CaptureController();
    await controller.start("Test meeting");

    const stopPromise = controller.stop();

    // stop() releases media before it calls finalize, so once finalize has
    // been invoked the microphone must already have been released.
    await vi.waitFor(() => {
      expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalled();
    });
    expect(sources.release).toHaveBeenCalledTimes(1);
    expect(controller.getState().status).toBe("finalizing");

    resolveFinalize({ id: "rec-1", status: "QUEUED" });
    await stopPromise;

    // disposeRuntime must not release the media a second time.
    expect(sources.release).toHaveBeenCalledTimes(1);
    expect(controller.getState().status).toBe("idle");
  });

  it("waits for the uploader to drain before pausing", async () => {
    const calls: string[] = [];
    apiMocks.pauseRecordingCapture.mockImplementation(async () => {
      calls.push("api");
      return {
        recording_id: "rec-1",
        status: "PAUSED",
        last_sequence: 4,
      };
    });

    const controller = new CaptureController() as any;
    controller.state = {
      ...controller.getState(),
      status: "recording",
      recordingId: "rec-1",
      elapsedSeconds: 0,
      lastSequence: -1,
    };
    controller.runtime = {
      recordingId: "rec-1",
      epoch: 0,
      recorder: {
        pause: async () => {
          calls.push("recorder");
        },
      },
      uploader: {
        recover: () => true,
        waitForIdle: async () => {
          calls.push("uploader");
        },
      },
    };

    await controller.pause();

    expect(calls).toEqual(["recorder", "uploader", "api"]);
  });

  it("surfaces the finalize detail instead of the raw axios status message", async () => {
    const detail =
      "Recording upload is still in progress; finalize after all segment uploads complete.";
    const controller = new CaptureController() as any;
    controller.state = {
      ...controller.getState(),
      status: "recording",
      recordingId: "rec-1",
    };
    controller.runtime = {
      recorder: {
        stop: async () => {},
      },
      uploader: {
        waitForIdle: async () => {},
        recover: () => true,
        dispose: () => {},
      },
      waveform: {
        stop: () => {},
      },
      sources: {
        release: () => {},
      },
      mixer: {
        dispose: async () => {},
      },
    };
    controller.finalizeRecordingWhenReady = vi.fn().mockRejectedValue(buildConflictError(detail));

    await expect(controller.stop()).rejects.toThrow(detail);
  });

  it("starts microphone-only capture with the detected mobile mode", async () => {
    const sources = {
      mode: "microphone_only",
      displayStream: null,
      microphoneStream: {} as MediaStream,
      captureReport: {
        mode: "microphone_only",
        requested_microphone_device_id: null,
        requested_microphone_label: null,
        available_microphones: [],
        browser_microphone_track: null,
        browser_display_audio_track: null,
        browser_display_video_track: null,
        shared_audio_available: false,
        notes: [],
      },
      release: vi.fn(),
    };
    featureDetectMocks.detectCaptureSupport.mockReturnValue({
      supported: true,
      mode: "microphone_only",
    });
    apiMocks.initRecording.mockResolvedValue({
      id: "rec-1",
      name: "Mobile meeting",
    });
    pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);

    const controller = new CaptureController() as any;
    controller.activateRuntime = vi.fn().mockResolvedValue(undefined);

    await expect(controller.start("Mobile meeting")).resolves.toEqual({
      recordingId: "rec-1",
      name: "Mobile meeting",
      resumed: false,
    });

    expect(pickSourceMocks.pickCaptureSource).toHaveBeenCalledWith({
      mode: "microphone_only",
      microphoneDeviceId: null,
      settings: {
        microphoneDeviceId: null,
        microphoneGain: 1,
        systemGain: 1,
        echoCancellation: true,
        noiseSuppression: false,
        autoGainControl: true,
      },
    });
    expect(controller.activateRuntime).toHaveBeenCalledWith({
      recordingId: "rec-1",
      startSequence: 0,
      sources,
      captureReport: sources.captureReport,
      elapsedSeconds: 0,
    });
  });

  it("silently rolls back when the display picker is cancelled", async () => {
    apiMocks.initRecording.mockResolvedValue({
      id: "rec-1",
      name: "Cancelled meeting",
    });
    pickSourceMocks.pickCaptureSource.mockRejectedValue(
      new PickSourceError(
        "display_cancelled",
        "Display capture was cancelled before the recording started.",
      ),
    );

    const controller = new CaptureController();

    await expect(controller.start("Cancelled meeting")).resolves.toBeNull();

    expect(apiMocks.discardRecordingCapture).toHaveBeenCalledWith(
      "rec-1",
      "display_picker_cancelled",
    );
    expect(controller.getState()).toMatchObject({
      status: "idle",
      error: null,
      recordingId: null,
      lastSequence: -1,
      elapsedSeconds: 0,
    });
  });

  it("auto-stops recording when a display track ends", async () => {
    const trackEndedListeners = new Set<() => void>();
    const mockTrack = {
      addEventListener: vi.fn((event, cb) => {
        if (event === "ended") trackEndedListeners.add(cb);
      }),
      removeEventListener: vi.fn((event, cb) => {
        if (event === "ended") trackEndedListeners.delete(cb);
      }),
      stop: vi.fn(),
    };

    const mockDisplayStream = {
      getAudioTracks: () => [mockTrack],
      getVideoTracks: () => [],
    };

    const sources = {
      mode: "shared_audio",
      displayStream: mockDisplayStream,
      microphoneStream: {} as MediaStream,
      captureReport: {
        mode: "shared_audio",
        requested_microphone_device_id: null,
        requested_microphone_label: null,
        available_microphones: [],
        browser_microphone_track: null,
        browser_display_audio_track: null,
        browser_display_video_track: null,
        shared_audio_available: true,
        notes: [],
      },
      release: vi.fn(),
    };

    pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);
    apiMocks.initRecording.mockResolvedValue({ id: "rec-123", name: "Test meeting" });

    const controller = new CaptureController();
    await controller.start("Test meeting");

    expect(controller.getState().status).toBe("recording");
    expect(mockTrack.addEventListener).toHaveBeenCalledWith("ended", expect.any(Function));

    apiMocks.finalizeRecordingCapture.mockResolvedValue({ id: "rec-123", status: "QUEUED" });

    trackEndedListeners.forEach((cb) => cb());

    expect(controller.getState().status).toBe("finalizing");

    await vi.waitFor(() => {
      expect(controller.getState().status).toBe("idle");
    });

    expect(notificationMocks.addNotification).toHaveBeenCalledWith({
      type: "info",
      message: "Screen sharing ended. The recording has been stopped and saved.",
    });
  });

  it("does not auto-stop when the recording is already finalizing", async () => {
    const trackEndedListeners = new Set<() => void>();
    const mockTrack = {
      addEventListener: vi.fn((event, cb) => {
        if (event === "ended") trackEndedListeners.add(cb);
      }),
      removeEventListener: vi.fn((event, cb) => {
        if (event === "ended") trackEndedListeners.delete(cb);
      }),
      stop: vi.fn(),
    };

    const mockDisplayStream = {
      getAudioTracks: () => [mockTrack],
      getVideoTracks: () => [],
    };

    const sources = {
      mode: "shared_audio",
      displayStream: mockDisplayStream,
      microphoneStream: {} as MediaStream,
      captureReport: {
        mode: "shared_audio",
        requested_microphone_device_id: null,
        requested_microphone_label: null,
        available_microphones: [],
        browser_microphone_track: null,
        browser_display_audio_track: null,
        browser_display_video_track: null,
        shared_audio_available: true,
        notes: [],
      },
      release: vi.fn(),
    };

    pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);
    apiMocks.initRecording.mockResolvedValue({ id: "rec-123", name: "Test meeting" });

    const controller = new CaptureController();
    await controller.start("Test meeting");

    let resolveFinalize: any;
    const finalizePromise = new Promise((resolve) => {
      resolveFinalize = resolve;
    });
    apiMocks.finalizeRecordingCapture.mockReturnValue(finalizePromise);

    const stopPromise = controller.stop();
    expect(controller.getState().status).toBe("finalizing");

    const stopSpy = vi.spyOn(controller, "stop");

    trackEndedListeners.forEach((cb) => cb());

    expect(stopSpy).not.toHaveBeenCalled();

    resolveFinalize({ id: "rec-123", status: "QUEUED" });
    await stopPromise;
  });

  it("does not attach listeners when displayStream is null (microphone-only)", async () => {
    const sources = {
      mode: "microphone_only",
      displayStream: null,
      microphoneStream: {} as MediaStream,
      captureReport: {
        mode: "microphone_only",
        requested_microphone_device_id: null,
        requested_microphone_label: null,
        available_microphones: [],
        browser_microphone_track: null,
        browser_display_audio_track: null,
        browser_display_video_track: null,
        shared_audio_available: false,
        notes: [],
      },
      release: vi.fn(),
    };

    pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);
    apiMocks.initRecording.mockResolvedValue({ id: "rec-123", name: "Test meeting" });

    const controller = new CaptureController();
    await controller.start("Test meeting");

    expect(controller.getState().status).toBe("recording");
  });

  it("cleans up display track listeners on disposeRuntime", async () => {
    const trackEndedListeners = new Set<() => void>();
    const mockTrack = {
      addEventListener: vi.fn((event, cb) => {
        if (event === "ended") trackEndedListeners.add(cb);
      }),
      removeEventListener: vi.fn((event, cb) => {
        if (event === "ended") trackEndedListeners.delete(cb);
      }),
      stop: vi.fn(),
    };

    const mockDisplayStream = {
      getAudioTracks: () => [mockTrack],
      getVideoTracks: () => [],
    };

    const sources = {
      mode: "shared_audio",
      displayStream: mockDisplayStream,
      microphoneStream: {} as MediaStream,
      captureReport: {
        mode: "shared_audio",
        requested_microphone_device_id: null,
        requested_microphone_label: null,
        available_microphones: [],
        browser_microphone_track: null,
        browser_display_audio_track: null,
        browser_display_video_track: null,
        shared_audio_available: true,
        notes: [],
      },
      release: vi.fn(),
    };

    pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);
    apiMocks.initRecording.mockResolvedValue({ id: "rec-123", name: "Test meeting" });
    apiMocks.finalizeRecordingCapture.mockResolvedValue({ id: "rec-123", status: "QUEUED" });

    const controller = new CaptureController();
    await controller.start("Test meeting");

    expect(mockTrack.addEventListener).toHaveBeenCalledWith("ended", expect.any(Function));

    await controller.stop();

    expect(mockTrack.removeEventListener).toHaveBeenCalledWith("ended", expect.any(Function));
  });

  it("finalizes a paused recording whose runtime is gone, without resuming first", async () => {
    // The guarded-exit case from issue #166: the runtime was disposed, so the
    // only route to processing is finalizing the uploaded segments directly.
    apiMocks.finalizeRecordingCapture.mockResolvedValue({
      id: "rec-1",
      status: "QUEUED",
    });

    const controller = new CaptureController() as any;
    controller.state = {
      ...controller.getState(),
      status: "paused",
      recordingId: "rec-1",
      lastSequence: 41,
    };
    controller.runtime = null;

    await expect(controller.stop()).resolves.toEqual({
      id: "rec-1",
      status: "QUEUED",
    });

    expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalledWith("rec-1", expect.any(Object));
    // Resuming would re-prompt the browser share picker for nothing, and the
    // ordering race around it was what stranded the recording.
    expect(apiMocks.resumeRecordingCapture).not.toHaveBeenCalled();
    expect(controller.getState().status).toBe("idle");
  });

  it("finalizes a paused recording identified only by the server", async () => {
    // A fresh tab has no sessionStorage context, so the id has to be passed in.
    apiMocks.finalizeRecordingCapture.mockResolvedValue({
      id: "rec-9",
      status: "QUEUED",
    });

    const controller = new CaptureController();

    await expect(controller.stop("rec-9")).resolves.toEqual({
      id: "rec-9",
      status: "QUEUED",
    });
    expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalledWith("rec-9", {
      clientElapsedSeconds: undefined,
    });
  });

  it("never leaves the controller stuck in finalizing when stop fails", async () => {
    // "finalizing" disables every transport control, so settling there bricked
    // the UI with no way back to Stop, Resume, or Discard.
    apiMocks.finalizeRecordingCapture.mockRejectedValue(
      new AxiosError("Network Error", "ERR_NETWORK"),
    );

    const controller = new CaptureController() as any;
    controller.state = {
      ...controller.getState(),
      status: "recording",
      recordingId: "rec-1",
    };
    controller.runtime = null;

    await expect(controller.stop()).rejects.toThrow("Network Error");

    const state = controller.getState();
    expect(state.status).toBe("paused");
    expect(state.stopStage).toBeNull();
    expect(state.recordingId).toBe("rec-1");
  });

  it("reports each stop stage in order", async () => {
    const sources = {
      mode: "microphone_only",
      displayStream: null,
      microphoneStream: {} as MediaStream,
      captureReport: {
        mode: "microphone_only",
        requested_microphone_device_id: null,
        requested_microphone_label: null,
        available_microphones: [],
        browser_microphone_track: null,
        browser_display_audio_track: null,
        browser_display_video_track: null,
        shared_audio_available: false,
        notes: [],
      },
      release: vi.fn(),
    };

    pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);
    apiMocks.initRecording.mockResolvedValue({ id: "rec-1", name: "Test meeting" });
    apiMocks.finalizeRecordingCapture.mockResolvedValue({
      id: "rec-1",
      status: "QUEUED",
    });

    const controller = new CaptureController();
    await controller.start("Test meeting");

    const stages: (string | null)[] = [];
    controller.subscribe((state) => {
      if (state.stopStage && stages[stages.length - 1] !== state.stopStage) {
        stages.push(state.stopStage);
      }
    });

    await controller.stop();

    // The media goes before the upload, which can take minutes.
    expect(stages).toEqual([
      "stopping-recorder",
      "releasing-media",
      "flushing-uploads",
      "finalizing",
    ]);
    expect(controller.getState().stopStage).toBeNull();
  });

  it("still finalizes when the uploader cannot drain", async () => {
    // Bounded, not fatal: the server refuses an incomplete upload with a
    // retryable 409, which beats hanging forever without calling finalize.
    const sources = {
      mode: "microphone_only",
      displayStream: null,
      microphoneStream: {} as MediaStream,
      captureReport: {
        mode: "microphone_only",
        requested_microphone_device_id: null,
        requested_microphone_label: null,
        available_microphones: [],
        browser_microphone_track: null,
        browser_display_audio_track: null,
        browser_display_video_track: null,
        shared_audio_available: false,
        notes: [],
      },
      release: vi.fn(),
    };

    pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);
    apiMocks.initRecording.mockResolvedValue({ id: "rec-1", name: "Test meeting" });
    apiMocks.finalizeRecordingCapture.mockResolvedValue({
      id: "rec-1",
      status: "QUEUED",
    });

    const controller = new CaptureController() as any;
    await controller.start("Test meeting");
    controller.runtime.uploader.waitForIdle = vi.fn(async () => {
      throw new Error("segment 12 never uploaded");
    });

    await expect(controller.stop()).resolves.toEqual({
      id: "rec-1",
      status: "QUEUED",
    });
    expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalledWith("rec-1", expect.any(Object));
  });

  /**
   * Coverage is judged against the audio the server reports holding, never
   * against sequence arithmetic. Multiplying the last sequence by the timeslice
   * under-counted captured audio by ~10% over an hour, because each segment
   * carries a little more than the nominal timeslice, and that alone was enough
   * to report minutes missing from a recording that had lost nothing.
   */
  const recordingWithCapturedAudio = (capturedSeconds: number, elapsedSeconds: number) => {
    const controller = new CaptureController() as any;
    controller.state = {
      ...controller.getState(),
      status: "recording",
      recordingId: "rec-1",
      elapsedSeconds,
    };
    controller.capturedAudioSeconds = capturedSeconds;
    return controller;
  };

  it("warns when the audio the server holds falls behind elapsed time", () => {
    // The 6 August 2026 shape: 81 minutes elapsed, 77 minutes of audio.
    const controller = recordingWithCapturedAudio(4_618, 4_874);

    controller.evaluateCoverage();

    const warning = controller.getState().coverageWarning;
    expect(warning).not.toBeNull();
    expect(warning.capturedSeconds).toBe(4_618);
    expect(warning.missingSeconds).toBe(256);
    expect(notificationMocks.addNotification).toHaveBeenCalledWith(
      expect.objectContaining({ type: "warning" }),
    );
  });

  it("stays quiet when captured audio only trails by upload latency", () => {
    const controller = recordingWithCapturedAudio(598, 604);

    controller.evaluateCoverage();

    expect(controller.getState().coverageWarning).toBeNull();
    expect(notificationMocks.addNotification).not.toHaveBeenCalled();
  });

  it("stays quiet before the server has reported any captured audio", () => {
    // Silence beats guessing. Guessing is what produced the false warnings.
    const controller = new CaptureController() as any;
    controller.state = {
      ...controller.getState(),
      status: "recording",
      recordingId: "rec-1",
      elapsedSeconds: 4_874,
    };

    controller.evaluateCoverage();

    expect(controller.getState().coverageWarning).toBeNull();
  });

  it("blames the backend when it is unreachable", () => {
    const controller = recordingWithCapturedAudio(4_618, 4_874);
    connectivityMocks.status = "unreachable";

    controller.evaluateCoverage();

    expect(controller.getState().coverageWarning.cause).toBe("backend-unreachable");
    expect(notificationMocks.addNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringContaining("cannot reach the server"),
      }),
    );
  });

  it("blames the tab only when the recorder was seen to stall", () => {
    const controller = recordingWithCapturedAudio(4_618, 4_874);
    controller.handleRecorderStall({ sinceLastChunkMs: 120_000, abandonedSegment: false });

    expect(controller.getState().coverageWarning.cause).toBe("tab-suspended");
  });

  it("does not blame the tab without evidence", () => {
    // A shortfall with neither signal present is reported without a diagnosis,
    // rather than asserting suspension and sending someone to browser settings.
    const controller = recordingWithCapturedAudio(4_618, 4_874);

    controller.evaluateCoverage();

    expect(controller.getState().coverageWarning.cause).toBe("unknown");
  });

  it("an unreachable backend outranks an earlier tab freeze", () => {
    // Both can be true at once; the actionable one is the live condition.
    const controller = recordingWithCapturedAudio(4_618, 4_874);
    controller.sawCaptureFreeze = true;
    connectivityMocks.status = "unreachable";

    controller.evaluateCoverage();

    expect(controller.getState().coverageWarning.cause).toBe("backend-unreachable");
  });

  it("does not count the upload queue as missing audio", () => {
    // The production shape: the server stalled for two minutes, the recorder
    // kept going, and the queue held everything it produced. Nothing was lost,
    // so nothing should be claimed lost.
    const controller = recordingWithCapturedAudio(4_618, 4_874);
    controller.runtime = {
      uploader: { pendingAudioSeconds: () => 256 },
    };

    controller.evaluateCoverage();

    expect(controller.getState().coverageWarning).toBeNull();
    expect(notificationMocks.addNotification).not.toHaveBeenCalled();
  });

  it("reports the queue alongside a genuine shortfall", () => {
    const controller = recordingWithCapturedAudio(4_618, 4_874);
    controller.runtime = {
      uploader: { pendingAudioSeconds: () => 30 },
    };

    controller.evaluateCoverage();

    const warning = controller.getState().coverageWarning;
    expect(warning.queuedSeconds).toBe(30);
    expect(warning.missingSeconds).toBe(226);
    expect(notificationMocks.addNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringContaining("waiting to upload"),
      }),
    );
  });

  it("still blames an outage that ended while the queue was draining", () => {
    // A shortfall is noticed by a 15-second poll and persists while the queue
    // drains, so the backend is usually reachable again by the time the warning
    // is raised. Classifying on the live status alone reported `unknown` for
    // the one case the classifier exists to name.
    const controller = recordingWithCapturedAudio(4_618, 4_874);
    connectivityMocks.status = "unreachable";
    controller.evaluateCoverage();
    expect(controller.getState().coverageWarning.cause).toBe("backend-unreachable");

    connectivityMocks.status = "online";
    controller.setState({ coverageWarning: null });
    controller.evaluateCoverage();

    expect(controller.getState().coverageWarning.cause).toBe("backend-unreachable");
  });

  it("clears the warning when dismissed", () => {
    const controller = recordingWithCapturedAudio(4_618, 4_874);
    controller.evaluateCoverage();

    controller.dismissCoverageWarning();

    expect(controller.getState().coverageWarning).toBeNull();
    expect(controller.getState().coverageWarningDismissedAt).toBe(256);
  });

  it("stays dismissed while the shortfall holds steady", () => {
    const controller = recordingWithCapturedAudio(4_618, 4_874);
    controller.evaluateCoverage();
    controller.dismissCoverageWarning();

    controller.evaluateCoverage();

    expect(controller.getState().coverageWarning).toBeNull();
  });

  it("returns when the shortfall grows materially past the dismissal", () => {
    const controller = recordingWithCapturedAudio(4_618, 4_874);
    controller.evaluateCoverage();
    controller.dismissCoverageWarning();

    // Another two minutes lost.
    controller.state = { ...controller.getState(), elapsedSeconds: 5_000 };
    controller.evaluateCoverage();

    expect(controller.getState().coverageWarning).not.toBeNull();
    expect(controller.getState().coverageWarningDismissedAt).toBeNull();
  });

  it("polls the backend for captured audio and re-judges", async () => {
    apiMocks.getRecording.mockResolvedValue({
      id: "rec-1",
      captured_audio_seconds: 4_618,
    });
    const controller = new CaptureController() as any;
    controller.state = {
      ...controller.getState(),
      status: "recording",
      recordingId: "rec-1",
      elapsedSeconds: 4_874,
    };

    await controller.pollCapturedAudio();

    expect(apiMocks.getRecording).toHaveBeenCalledWith("rec-1");
    expect(controller.getState().coverageWarning.missingSeconds).toBe(256);
  });

  it("a failed poll changes nothing", async () => {
    // The connectivity monitor owns "is the backend reachable"; a failed poll
    // here is not evidence of a shortfall.
    apiMocks.getRecording.mockRejectedValue(new Error("network"));
    const controller = recordingWithCapturedAudio(4_618, 4_874);

    await controller.pollCapturedAudio();

    expect(controller.getState().coverageWarning).toBeNull();
  });
});

describe("recovering an interrupted capture", () => {
  const PAUSED = { id: "rec-1", name: "Meeting" };

  const controllerWithPaused = () => {
    const controller = new CaptureController() as any;
    controller.state = { ...controller.getState(), pausedRecording: PAUSED };
    return controller;
  };

  beforeEach(() => {
    apiMocks.discardRecordingCapture.mockReset();
    apiMocks.discardRecordingCapture.mockResolvedValue(undefined);
    apiMocks.getUnfinishedCaptures.mockReset();
    apiMocks.getUnfinishedCaptures.mockResolvedValue([]);
    sharedMocks.readPausedCaptureContext.mockReset();
    sharedMocks.readPausedCaptureContext.mockReturnValue(null);
    notificationMocks.addNotification.mockReset();
  });

  it("discards a capture this tab lost before banking any audio", async () => {
    // What a guarded exit leaves behind when the app's own post-start
    // navigation unloads the page a second after the recording began.
    sharedMocks.readPausedCaptureContext.mockReturnValue({
      recordingId: "rec-1",
      lastSequence: -1,
      persistedAt: 1,
    });
    const controller = controllerWithPaused();

    await expect(controller.discardEmptyInterruptedRecording()).resolves.toBe(
      true,
    );
    expect(apiMocks.discardRecordingCapture).toHaveBeenCalledWith("rec-1", undefined, {
      epoch: undefined,
    });
    expect(controller.getState().pausedRecording).toBeNull();
    expect(notificationMocks.addNotification).toHaveBeenCalledWith(
      expect.objectContaining({ type: "warning" }),
    );
  });

  it("keeps a capture that banked a segment for the user to decide about", async () => {
    sharedMocks.readPausedCaptureContext.mockReturnValue({
      recordingId: "rec-1",
      lastSequence: 0,
      persistedAt: 1,
    });
    const controller = controllerWithPaused();

    await expect(controller.discardEmptyInterruptedRecording()).resolves.toBe(
      false,
    );
    expect(apiMocks.discardRecordingCapture).not.toHaveBeenCalled();
    expect(controller.getState().pausedRecording).toEqual(PAUSED);
  });

  it("leaves a paused recording this tab never owned alone", async () => {
    // No persisted context: the pause came from another tab, another device, or
    // an explicit pause, none of which this tab may resolve on the user's behalf.
    const controller = controllerWithPaused();

    await expect(controller.discardEmptyInterruptedRecording()).resolves.toBe(
      false,
    );
    expect(apiMocks.discardRecordingCapture).not.toHaveBeenCalled();
  });

  it("ignores a context naming a different recording", async () => {
    sharedMocks.readPausedCaptureContext.mockReturnValue({
      recordingId: "rec-other",
      lastSequence: -1,
      persistedAt: 1,
    });
    const controller = controllerWithPaused();

    await expect(controller.discardEmptyInterruptedRecording()).resolves.toBe(
      false,
    );
    expect(apiMocks.discardRecordingCapture).not.toHaveBeenCalled();
  });

  it("announces the removal so every list drops the row at once", async () => {
    const removed: string[] = [];
    const listener = (event: Event) => {
      removed.push((event as CustomEvent<{ id: string }>).detail.id);
    };
    window.addEventListener("recording-removed", listener);

    try {
      const controller = new CaptureController() as any;
      await controller.cancel("rec-1");
    } finally {
      window.removeEventListener("recording-removed", listener);
    }

    expect(removed).toEqual(["rec-1"]);
  });
});

describe("audio a closed tab recorded but never uploaded", () => {
  const stored = (sequence: number) => ({
    recordingId: "rec-1",
    sequence,
    blob: new Blob([String(sequence)], { type: "audio/flac" }),
    durationSeconds: 2,
  });

  const sources = {
    mode: "microphone_only",
    displayStream: null,
    microphoneStream: {} as MediaStream,
    captureReport: {
      mode: "microphone_only",
      requested_microphone_device_id: null,
      requested_microphone_label: null,
      available_microphones: [],
      browser_microphone_track: null,
      browser_display_audio_track: null,
      browser_display_video_track: null,
      shared_audio_available: false,
      notes: [],
    },
    release: vi.fn(),
  };

  const controllerWithStored = async (sequences: number[]) => {
    const controller = new CaptureController() as any;
    for (const sequence of sequences) {
      await controller.segmentStore.put(stored(sequence));
    }
    return controller;
  };

  const storedSequences = async (controller: any) =>
    (await controller.segmentStore.list("rec-1")).map(
      (segment: { sequence: number }) => segment.sequence,
    );

  beforeEach(() => {
    vi.mocked(createSegmentUploader).mockClear();
    vi.mocked(createBrowserRecorder).mockClear();
    apiMocks.finalizeRecordingCapture.mockReset();
    apiMocks.finalizeRecordingCapture.mockResolvedValue({ id: "rec-1", status: "QUEUED" });
    apiMocks.pauseRecordingCapture.mockReset();
    apiMocks.pauseRecordingCapture.mockResolvedValue({
      recording_id: "rec-1",
      status: "PAUSED",
      last_sequence: 4,
      capture_epoch: 0,
    });
    apiMocks.resumeRecordingCapture.mockReset();
    // Taken over from the closed tab, which held epoch 0.
    apiMocks.resumeRecordingCapture.mockResolvedValue({
      recording_id: "rec-1",
      status: "UPLOADING",
      last_sequence: 4,
      capture_epoch: 1,
    });
    apiMocks.discardRecordingCapture.mockReset();
    apiMocks.discardRecordingCapture.mockResolvedValue(undefined);
    apiMocks.getUnfinishedCaptures.mockReset();
    apiMocks.getUnfinishedCaptures.mockResolvedValue([]);
    apiMocks.reportRecordingCaptureSources.mockResolvedValue(undefined);
    pickSourceMocks.pickCaptureSource.mockReset();
    pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);
    featureDetectMocks.detectCaptureSupport.mockReturnValue({
      supported: true,
      mode: "microphone_only",
    });
    sharedMocks.readPausedCaptureContext.mockReset();
    sharedMocks.readPausedCaptureContext.mockReturnValue(null);
  });

  it("uploads it ahead of new audio on resume, and numbers new audio after it", async () => {
    // 3 is already on the server and 9 sits past a gap; neither can be sent.
    const controller = await controllerWithStored([3, 5, 6, 9]);

    await controller.resume("rec-1");

    const uploaderOptions = vi.mocked(createSegmentUploader).mock.calls[0][0];
    const uploader = vi.mocked(createSegmentUploader).mock.results[0].value;
    expect(uploaderOptions.initialSequence).toBe(5);
    expect(uploader.restore.mock.calls[0][0].map((s: { sequence: number }) => s.sequence)).toEqual([
      5, 6,
    ]);
    expect(vi.mocked(createBrowserRecorder).mock.calls[0][0].startSequence).toBe(7);
    await vi.waitFor(async () => expect(await storedSequences(controller)).toEqual([5, 6]));
  });

  it("uploads it before finalizing a stop with no live capture", async () => {
    const controller = await controllerWithStored([5, 6]);
    const order: string[] = [];
    vi.mocked(createSegmentUploader).mockImplementationOnce(
      () =>
        ({
          enqueue: vi.fn(),
          restore: vi.fn(() => order.push("restore")),
          waitForIdle: vi.fn(async () => {
            order.push("drained");
          }),
          recover: vi.fn(() => true),
          dispose: vi.fn(),
          pendingSegmentCount: vi.fn(() => 0),
          pendingAudioSeconds: vi.fn(() => 0),
        }) as never,
    );
    apiMocks.finalizeRecordingCapture.mockImplementation(async () => {
      order.push("finalize");
      return { id: "rec-1", status: "QUEUED" };
    });

    await controller.stop("rec-1");

    expect(vi.mocked(createSegmentUploader).mock.calls[0][0].initialSequence).toBe(5);
    expect(order).toEqual(["restore", "drained", "finalize"]);
    expect(await storedSequences(controller)).toEqual([]);
  });

  it("finalizes straight away when nothing was left behind", async () => {
    const controller = await controllerWithStored([]);

    await controller.stop("rec-1");

    expect(createSegmentUploader).not.toHaveBeenCalled();
    expect(apiMocks.pauseRecordingCapture).not.toHaveBeenCalled();
    expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalledTimes(1);
  });

  it("keeps an interrupted capture whose only audio is still in the browser", async () => {
    sharedMocks.readPausedCaptureContext.mockReturnValue({
      recordingId: "rec-1",
      lastSequence: -1,
      persistedAt: 1,
    });
    const controller = await controllerWithStored([0, 1]);
    controller.state = { ...controller.getState(), pausedRecording: { id: "rec-1" } };

    await expect(controller.discardEmptyInterruptedRecording()).resolves.toBe(false);
    expect(apiMocks.discardRecordingCapture).not.toHaveBeenCalled();
  });

  it("forgets it when the recording is discarded", async () => {
    const controller = await controllerWithStored([5, 6]);

    await controller.cancel("rec-1");

    expect(await storedSequences(controller)).toEqual([]);
  });
});

describe("a capture whose session ends", () => {
  const microphoneSources = () => ({
    mode: "microphone_only",
    displayStream: null,
    microphoneStream: {} as MediaStream,
    captureReport: {
      mode: "microphone_only",
      requested_microphone_device_id: null,
      requested_microphone_label: null,
      available_microphones: [],
      browser_microphone_track: null,
      browser_display_audio_track: null,
      browser_display_video_track: null,
      shared_audio_available: false,
      notes: [],
    },
    release: vi.fn(),
  });

  /** A 401 as the API client hands it on, after its interceptor has seen it. */
  const sessionEnded = () => {
    const error = new AxiosError("Request failed with status code 401");
    Object.assign(error, { response: { status: 401, data: { detail: "Not authenticated" } } });
    noteSessionRefusal(error);
    return error;
  };

  const signBackIn = () => noteSignedIn({ username: "alice", force_password_change: false });

  let sources = microphoneSources();

  const startCapture = async () => {
    sources = microphoneSources();
    pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);
    apiMocks.initRecording.mockResolvedValue({ id: "rec-1", name: "Test meeting" });
    const controller = new CaptureController();
    await controller.start("Test meeting");
    return controller;
  };

  beforeEach(() => {
    resetSessionForTests();
    signBackIn();
    apiMocks.initRecording.mockReset();
    apiMocks.finalizeRecordingCapture.mockReset();
    apiMocks.pauseRecordingCapture.mockReset();
    apiMocks.getUnfinishedCaptures.mockResolvedValue([]);
    apiMocks.reportRecordingCaptureSources.mockResolvedValue(undefined);
    pickSourceMocks.pickCaptureSource.mockReset();
  });

  afterEach(() => {
    resetSessionForTests();
  });

  it("holds sign-in in place from start until the recording is finalized", async () => {
    apiMocks.finalizeRecordingCapture.mockResolvedValue({ id: "rec-1", status: "QUEUED" });
    expect(useSessionStore.getState().heldInPlace).toBe(false);

    const controller = await startCapture();
    expect(useSessionStore.getState().heldInPlace).toBe(true);

    await controller.stop();
    expect(useSessionStore.getState().heldInPlace).toBe(false);
  });

  it("finishes a stop once the user signs back in", async () => {
    apiMocks.finalizeRecordingCapture
      .mockImplementationOnce(async () => {
        throw sessionEnded();
      })
      .mockResolvedValue({ id: "rec-1", status: "QUEUED" });
    const controller = await startCapture();

    const stopped = controller.stop();
    await vi.waitFor(() => expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 0));

    // Still stopping, and still held, rather than failed back to paused.
    expect(controller.getState().status).toBe("finalizing");
    expect(useSessionStore.getState().heldInPlace).toBe(true);

    signBackIn();

    await expect(stopped).resolves.toEqual({ id: "rec-1", status: "QUEUED" });
    expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalledTimes(2);
    expect(controller.getState().status).toBe("idle");
  });

  it("lets go of the microphone and shared tab before the upload waits for sign-in", async () => {
    apiMocks.finalizeRecordingCapture.mockResolvedValue({ id: "rec-1", status: "QUEUED" });
    vi.mocked(createSegmentUploader).mockClear();
    const controller = await startCapture();
    const uploader = vi.mocked(createSegmentUploader).mock.results[0].value;
    let uploadsDone = () => {};
    // The queue waits for the user to sign back in, however long that takes.
    vi.mocked(uploader.waitForIdle).mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          uploadsDone = resolve;
        }),
    );

    const stopped = controller.stop();
    await vi.waitFor(() => expect(controller.getState().stopStage).toBe("flushing-uploads"));

    expect(sources.release).toHaveBeenCalledTimes(1);
    expect(controller.getState().status).toBe("finalizing");
    expect(apiMocks.finalizeRecordingCapture).not.toHaveBeenCalled();

    uploadsDone();

    await expect(stopped).resolves.toEqual({ id: "rec-1", status: "QUEUED" });
  });

  it("pauses on the server once the user signs back in", async () => {
    apiMocks.pauseRecordingCapture
      .mockImplementationOnce(async () => {
        throw sessionEnded();
      })
      .mockResolvedValue({ recording_id: "rec-1", status: "PAUSED", last_sequence: 3 });
    const controller = await startCapture();

    const paused = controller.pause();
    await vi.waitFor(() => expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    // The tab reads paused at once; the server is told once the user is back.
    expect(controller.getState().status).toBe("paused");
    expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(1);

    signBackIn();
    await paused;

    expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(2);
    expect(controller.getState().status).toBe("paused");
    expect(controller.getState().error).toBeNull();
  });
});

describe("a capture whose tab closed without pausing it (issue #519)", () => {
  // What the server lists for a capture whose unload pause never arrived: it
  // is still marked as uploading, and the server says whether it has stalled.
  const capture = (overrides: Record<string, unknown> = {}) => ({
    id: "rec-1",
    name: "Weekly sync",
    status: "UPLOADING",
    client_status: "RECORDING",
    capture_stalled: false,
    ...overrides,
  });

  const microphoneSources = () => ({
    mode: "microphone_only",
    displayStream: null,
    microphoneStream: {} as MediaStream,
    captureReport: {
      mode: "microphone_only",
      requested_microphone_device_id: null,
      requested_microphone_label: null,
      available_microphones: [],
      browser_microphone_track: null,
      browser_display_audio_track: null,
      browser_display_video_track: null,
      shared_audio_available: false,
      notes: [],
    },
    release: vi.fn(),
  });

  const controllers: CaptureController[] = [];
  const newController = () => {
    const controller = new CaptureController();
    controllers.push(controller);
    return controller;
  };

  beforeEach(() => {
    resetSessionForTests();
    apiMocks.getUnfinishedCaptures.mockReset();
    apiMocks.getUnfinishedCaptures.mockResolvedValue([]);
    apiMocks.discardRecordingCapture.mockReset();
    apiMocks.discardRecordingCapture.mockResolvedValue(undefined);
    apiMocks.initRecording.mockReset();
    apiMocks.reportRecordingCaptureSources.mockResolvedValue(undefined);
    pickSourceMocks.pickCaptureSource.mockReset();
    featureDetectMocks.detectCaptureSupport.mockReturnValue({
      supported: true,
      mode: "microphone_only",
    });
    sharedMocks.readPausedCaptureContext.mockReset();
    sharedMocks.readPausedCaptureContext.mockReturnValue(null);
    captureLockMocks.release.mockClear();
    captureLockMocks.holdCaptureLock.mockClear();
    captureLockMocks.isCaptureHeldInThisBrowser.mockReset();
    captureLockMocks.isCaptureHeldInThisBrowser.mockResolvedValue(false);
  });

  afterEach(async () => {
    // Each controller left watching would keep its timer running.
    await Promise.all(controllers.splice(0).map((controller) => controller.destroy()));
    vi.useRealTimers();
  });

  it("offers it for resume, stop or discard once the server judges it stalled", async () => {
    const stalled = capture({ capture_stalled: true });
    apiMocks.getUnfinishedCaptures.mockResolvedValue([stalled]);
    const controller = newController();

    await expect(controller.refreshPausedRecording()).resolves.toEqual(stalled);

    expect(controller.getState().pausedRecording).toEqual(stalled);
  });

  it("never offers a capture that is still uploading", async () => {
    apiMocks.getUnfinishedCaptures.mockResolvedValue([capture()]);
    const controller = newController();

    await expect(controller.refreshPausedRecording()).resolves.toBeNull();

    expect(controller.getState().pausedRecording).toBeNull();
  });

  it("never offers a stalled capture that another tab of this browser is running", async () => {
    // That tab may only be waiting for a sign-in or a connection, with its
    // audio kept in the browser. It still holds the capture lock.
    apiMocks.getUnfinishedCaptures.mockResolvedValue([capture({ capture_stalled: true })]);
    captureLockMocks.isCaptureHeldInThisBrowser.mockResolvedValue(true);
    const controller = newController();

    await expect(controller.refreshPausedRecording()).resolves.toBeNull();

    expect(captureLockMocks.isCaptureHeldInThisBrowser).toHaveBeenCalledWith("rec-1");
    expect(controller.getState().pausedRecording).toBeNull();
  });

  it("ignores an import, which also sits in UPLOADING", async () => {
    vi.useFakeTimers();
    apiMocks.getUnfinishedCaptures.mockResolvedValue([
      capture({ client_status: undefined, capture_stalled: false }),
    ]);
    const controller = newController();

    await expect(controller.refreshPausedRecording()).resolves.toBeNull();
    await vi.advanceTimersByTimeAsync(120_000);

    // Nothing to wait for, so nothing to look at again.
    expect(apiMocks.getUnfinishedCaptures).toHaveBeenCalledTimes(1);
  });

  it("offers it without a reload once it stalls", async () => {
    vi.useFakeTimers();
    apiMocks.getUnfinishedCaptures
      .mockResolvedValueOnce([capture()])
      .mockResolvedValue([capture({ capture_stalled: true })]);
    const controller = newController();

    await controller.refreshPausedRecording();
    expect(controller.getState().pausedRecording).toBeNull();

    await vi.advanceTimersByTimeAsync(30_000);

    expect(apiMocks.getUnfinishedCaptures).toHaveBeenCalledTimes(2);
    expect(controller.getState().pausedRecording).toMatchObject({
      id: "rec-1",
      capture_stalled: true,
    });
  });

  it("withdraws the offer by itself when the capture's own tab uploads again", async () => {
    vi.useFakeTimers();
    apiMocks.getUnfinishedCaptures
      .mockResolvedValueOnce([capture({ capture_stalled: true })])
      .mockResolvedValue([capture()]);
    const controller = newController();

    await controller.refreshPausedRecording();
    expect(controller.getState().pausedRecording).not.toBeNull();

    await vi.advanceTimersByTimeAsync(30_000);

    expect(controller.getState().pausedRecording).toBeNull();
  });

  it("stops looking once nothing is left unfinished", async () => {
    vi.useFakeTimers();
    apiMocks.getUnfinishedCaptures.mockResolvedValueOnce([capture()]).mockResolvedValue([]);
    const controller = newController();

    await controller.refreshPausedRecording();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(apiMocks.getUnfinishedCaptures).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(120_000);
    expect(apiMocks.getUnfinishedCaptures).toHaveBeenCalledTimes(2);
  });

  it("a check already in flight cannot bring back a capture the user discarded", async () => {
    const stalled = capture({ capture_stalled: true });
    apiMocks.getUnfinishedCaptures.mockResolvedValueOnce([stalled]);
    const controller = newController();
    await controller.refreshPausedRecording();

    let answerSlowCheck: (captures: unknown[]) => void = () => {};
    apiMocks.getUnfinishedCaptures.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answerSlowCheck = resolve;
        }),
    );
    const slowCheck = controller.refreshPausedRecording();
    apiMocks.getUnfinishedCaptures.mockResolvedValue([]);

    await controller.cancel("rec-1");
    answerSlowCheck([stalled]);
    await slowCheck;

    expect(apiMocks.discardRecordingCapture).toHaveBeenCalledWith("rec-1", undefined, {
      epoch: undefined,
    });
    expect(controller.getState().pausedRecording).toBeNull();
  });

  it("marks its own capture as live to other tabs until the capture ends", async () => {
    pickSourceMocks.pickCaptureSource.mockResolvedValue(microphoneSources());
    apiMocks.initRecording.mockResolvedValue({ id: "rec-1", name: "Weekly sync" });
    const controller = newController();

    await controller.start("Weekly sync");

    expect(captureLockMocks.holdCaptureLock).toHaveBeenCalledWith("rec-1");
    expect(captureLockMocks.release).not.toHaveBeenCalled();

    apiMocks.finalizeRecordingCapture.mockResolvedValue({ id: "rec-1", status: "QUEUED" });
    await controller.stop();

    expect(captureLockMocks.release).toHaveBeenCalledTimes(1);
  });

  it("never offers its own capture, however quiet the server finds it", async () => {
    pickSourceMocks.pickCaptureSource.mockResolvedValue(microphoneSources());
    apiMocks.initRecording.mockResolvedValue({ id: "rec-1", name: "Weekly sync" });
    const controller = newController();
    await controller.start("Weekly sync");
    apiMocks.getUnfinishedCaptures.mockResolvedValue([capture({ capture_stalled: true })]);

    await expect(controller.refreshPausedRecording()).resolves.toBeNull();
  });
});

describe("an outage that outlasts the upload retries (issue #568)", () => {
  const microphoneSources = () => ({
    mode: "microphone_only",
    displayStream: null,
    microphoneStream: {} as MediaStream,
    captureReport: {
      mode: "microphone_only",
      requested_microphone_device_id: null,
      requested_microphone_label: null,
      available_microphones: [],
      browser_microphone_track: null,
      browser_display_audio_track: null,
      browser_display_video_track: null,
      shared_audio_available: false,
      notes: [],
    },
    release: vi.fn(),
  });

  /** A request that never reached the API, as axios reports it. */
  const unreachable = () => new AxiosError("Network Error", "ERR_NETWORK");

  const paused = (lastSequence: number) => ({
    recording_id: "rec-1",
    status: "PAUSED",
    last_sequence: lastSequence,
    capture_epoch: 0,
  });

  const controllers: CaptureController[] = [];

  const startCapture = async () => {
    const sources = microphoneSources();
    pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);
    apiMocks.initRecording.mockResolvedValue({
      id: "rec-1",
      name: "Weekly sync",
      capture_epoch: 0,
    });
    const controller = new CaptureController();
    controllers.push(controller);
    await controller.start("Weekly sync");
    const uploaderOptions = vi.mocked(createSegmentUploader).mock.calls[0][0];
    const uploader = vi.mocked(createSegmentUploader).mock.results[0].value;
    const recorder = vi.mocked(createBrowserRecorder).mock.results[0].value;
    return { controller, sources, uploaderOptions, uploader, recorder };
  };

  /**
   * The uploader giving up on a segment after its retries, about 15 seconds
   * into an outage, while the pause the controller then sends fails as well.
   */
  const stallWithServerUnreachable = async (
    uploaderOptions: Parameters<typeof createSegmentUploader>[0],
  ) => {
    apiMocks.pauseRecordingCapture.mockRejectedValueOnce(unreachable());
    await uploaderOptions.onStalled?.(unreachable());
    await vi.waitFor(() => expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(1));
    // Let the failed attempt settle.
    await new Promise((resolve) => setTimeout(resolve, 0));
  };

  beforeEach(() => {
    resetSessionForTests();
    vi.mocked(createSegmentUploader).mockClear();
    vi.mocked(createBrowserRecorder).mockClear();
    apiMocks.initRecording.mockReset();
    apiMocks.pauseRecordingCapture.mockReset();
    apiMocks.pauseRecordingCapture.mockResolvedValue(paused(7));
    apiMocks.resumeRecordingCapture.mockReset();
    apiMocks.resumeRecordingCapture.mockResolvedValue({
      recording_id: "rec-1",
      status: "UPLOADING",
      last_sequence: 7,
      capture_epoch: 0,
    });
    apiMocks.finalizeRecordingCapture.mockReset();
    apiMocks.discardRecordingCapture.mockReset();
    apiMocks.discardRecordingCapture.mockResolvedValue(undefined);
    apiMocks.getUnfinishedCaptures.mockReset();
    apiMocks.getUnfinishedCaptures.mockResolvedValue([]);
    apiMocks.reportRecordingCaptureSources.mockResolvedValue(undefined);
    pickSourceMocks.pickCaptureSource.mockReset();
    featureDetectMocks.detectCaptureSupport.mockReturnValue({
      supported: true,
      mode: "microphone_only",
    });
    sharedMocks.readPausedCaptureContext.mockReset();
    sharedMocks.readPausedCaptureContext.mockReturnValue(null);
    connectivityMocks.status = "online";
    connectivityMocks.lastReachableAt = null;
    connectivityMocks.listeners.clear();
    captureLockMocks.release.mockClear();
  });

  afterEach(async () => {
    await Promise.all(controllers.splice(0).map((controller) => controller.destroy()));
    vi.useRealTimers();
    resetSessionForTests();
  });

  it("pauses the tab and keeps the capture when the server cannot be told", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    const { controller, sources, uploaderOptions, uploader, recorder } = await startCapture();
    await vi.advanceTimersByTimeAsync(20_000);

    await stallWithServerUnreachable(uploaderOptions);

    const state = controller.getState();
    expect(recorder.pause).toHaveBeenCalledTimes(1);
    // Paused, not failed: the controls offer resume and stop rather than a new meeting.
    expect(state.status).toBe("paused");
    expect(state.runtimeActive).toBe(true);
    expect(state.recordingId).toBe("rec-1");
    // The microphone, the queued audio and the capture lock are all kept.
    expect(sources.release).not.toHaveBeenCalled();
    expect(uploader.dispose).not.toHaveBeenCalled();
    expect(captureLockMocks.release).not.toHaveBeenCalled();

    // The clock stops with the recorder.
    const elapsed = state.elapsedSeconds;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(controller.getState().elapsedSeconds).toBe(elapsed);
  });

  it("tells the server once the API answers again, after sending the audio it kept", async () => {
    const { controller, uploaderOptions, uploader } = await startCapture();
    await stallWithServerUnreachable(uploaderOptions);
    const order: string[] = [];
    vi.mocked(uploader.recover).mockImplementation(() => {
      order.push("recover");
      return true;
    });
    vi.mocked(uploader.waitForIdle).mockImplementation(async () => {
      order.push("uploaded");
    });
    apiMocks.pauseRecordingCapture.mockImplementation(async () => {
      order.push("pause");
      return paused(9);
    });

    connectivityMocks.apiAnswered();

    await vi.waitFor(() => expect(order).toEqual(["recover", "uploaded", "pause"]));
    await vi.waitFor(() => expect(controller.getState().lastSequence).toBe(9));
    expect(controller.getState().status).toBe("paused");

    // Once the server has it, nothing more is sent.
    connectivityMocks.apiAnswered();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(2);
  });

  it("waits for an answer from the API newer than the failed attempt", async () => {
    const { uploaderOptions } = await startCapture();
    await stallWithServerUnreachable(uploaderOptions);

    // A success the monitor recorded before the pause failed proves nothing.
    connectivityMocks.apiAnswered(Date.now() - 60_000);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(1);

    connectivityMocks.apiAnswered();
    await vi.waitFor(() => expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(2));
  });

  it("resumes the same capture, pausing on the server first", async () => {
    const { controller, uploaderOptions, uploader, recorder } = await startCapture();
    await stallWithServerUnreachable(uploaderOptions);
    const order: string[] = [];
    apiMocks.pauseRecordingCapture.mockImplementation(async () => {
      order.push("pause");
      return paused(7);
    });
    apiMocks.resumeRecordingCapture.mockImplementation(async () => {
      order.push("resume");
      return { recording_id: "rec-1", status: "UPLOADING", last_sequence: 7 };
    });
    vi.mocked(uploader.recover).mockImplementation(() => {
      order.push("recover uploads");
      return true;
    });
    vi.mocked(recorder.resume).mockImplementation(() => {
      order.push("record");
    });

    await expect(controller.resume()).resolves.toEqual({ recordingId: "rec-1", resumed: true });

    // The server refuses to resume a capture it still holds as uploading, so
    // the pause it missed goes first. The kept audio goes before new audio.
    expect(order).toEqual(["pause", "resume", "recover uploads", "record"]);
    expect(controller.getState().status).toBe("recording");
    // The same capture carries on: no share picker, no second uploader.
    expect(pickSourceMocks.pickCaptureSource).toHaveBeenCalledTimes(1);
    expect(createSegmentUploader).toHaveBeenCalledTimes(1);
    expect(uploader.dispose).not.toHaveBeenCalled();
  });

  it("stays paused, still owing the pause, when resumed before the API is back", async () => {
    const { controller, uploaderOptions } = await startCapture();
    await stallWithServerUnreachable(uploaderOptions);
    apiMocks.pauseRecordingCapture.mockRejectedValueOnce(unreachable());

    await expect(controller.resume()).rejects.toThrow("Network Error");

    expect(apiMocks.resumeRecordingCapture).not.toHaveBeenCalled();
    expect(controller.getState().status).toBe("paused");
    expect(controller.getState().runtimeActive).toBe(true);

    // A second failure in a row holds the next attempt back 2 s (issue #599).
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    connectivityMocks.apiAnswered();
    await vi.advanceTimersByTimeAsync(1_500);
    expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(600);
    expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(3);
  });

  describe("while only the pause fails (issue #599)", () => {
    /** Every pause fails at the network, and records when it was sent. */
    const failEveryPause = () => {
      const sentAt: number[] = [];
      apiMocks.pauseRecordingCapture.mockImplementation(async () => {
        sentAt.push(Date.now());
        throw unreachable();
      });
      return sentAt;
    };

    it("backs off from 2 s to 30 s, however often the API answers", async () => {
      const { uploaderOptions } = await startCapture();
      await stallWithServerUnreachable(uploaderOptions);
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
      const sentAt = failEveryPause();

      // Each failure brings a probe forward, and it answers: twice a second.
      for (let tick = 0; tick < 600; tick += 1) {
        connectivityMocks.apiAnswered();
        await vi.advanceTimersByTimeAsync(500);
      }

      const gaps = sentAt.slice(1).map((at, index) => Math.round((at - sentAt[index]) / 1_000));
      // The first retry goes on the first answer, then 2, 4, 8 and 16 s, then
      // every 30 s: about 14 attempts in five minutes rather than about 600.
      expect(gaps.slice(0, 5)).toEqual([2, 4, 8, 16, 30]);
      expect(new Set(gaps.slice(5))).toEqual(new Set([30]));
      expect(sentAt.length).toBeLessThanOrEqual(15);
    });

    it("retries on the first answer after a silence longer than the backoff", async () => {
      const { uploaderOptions } = await startCapture();
      await stallWithServerUnreachable(uploaderOptions);
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
      const sentAt = failEveryPause();
      for (let tick = 0; tick < 60; tick += 1) {
        connectivityMocks.apiAnswered();
        await vi.advanceTimersByTimeAsync(500);
      }
      const attempts = sentAt.length;

      // An outage: nothing answers for a minute, then the API is back.
      await vi.advanceTimersByTimeAsync(60_000);
      apiMocks.pauseRecordingCapture.mockResolvedValue(paused(7));
      connectivityMocks.apiAnswered();
      await vi.advanceTimersByTimeAsync(0);

      expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(attempts + 2);
    });

    it("sends it at once for a resume, whatever the backoff", async () => {
      const { controller, uploaderOptions } = await startCapture();
      await stallWithServerUnreachable(uploaderOptions);
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
      failEveryPause();
      for (let tick = 0; tick < 20; tick += 1) {
        connectivityMocks.apiAnswered();
        await vi.advanceTimersByTimeAsync(500);
      }
      const attempts = apiMocks.pauseRecordingCapture.mock.calls.length;
      apiMocks.pauseRecordingCapture.mockResolvedValue(paused(7));

      await expect(controller.resume()).resolves.toEqual({ recordingId: "rec-1", resumed: true });

      expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(attempts + 1);
      expect(controller.getState().status).toBe("recording");
    });
  });

  it("still sends the pause after a long outage, carrying its epoch (issue #570)", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    const { controller, uploaderOptions } = await startCapture();
    await stallWithServerUnreachable(uploaderOptions);

    // Long enough for the server to have offered the capture elsewhere. The
    // epoch has the server refuse the pause if it was resumed there.
    await vi.advanceTimersByTimeAsync(5 * 60_000);
    connectivityMocks.apiAnswered();

    await vi.waitFor(() => expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(2));
    expect(apiMocks.pauseRecordingCapture).toHaveBeenLastCalledWith("rec-1", { epoch: 0 });
    await controller.resume();
    expect(apiMocks.resumeRecordingCapture).toHaveBeenCalledWith("rec-1", { epoch: 0 });
    expect(pickSourceMocks.pickCaptureSource).toHaveBeenCalledTimes(1);
    expect(controller.getState().status).toBe("recording");
  });

  it("stops asking once the server refuses the pause", async () => {
    const { controller, uploaderOptions } = await startCapture();
    const refused = new AxiosError("Request failed with status code 500");
    Object.assign(refused, {
      response: { status: 500, data: { detail: "The pause could not be saved." } },
    });
    apiMocks.pauseRecordingCapture.mockRejectedValue(refused);

    await uploaderOptions.onStalled?.(unreachable());
    await vi.waitFor(() => expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(1));
    connectivityMocks.apiAnswered();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(1);
    expect(controller.getState().status).toBe("paused");
  });

  it("stops asking once the recording is discarded", async () => {
    const { controller, uploaderOptions, recorder } = await startCapture();
    vi.mocked(recorder.stop).mockResolvedValue(undefined);
    await stallWithServerUnreachable(uploaderOptions);

    await controller.cancel();
    connectivityMocks.apiAnswered();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(apiMocks.discardRecordingCapture).toHaveBeenCalledWith("rec-1", undefined, {
      epoch: 0,
    });
    expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(1);
  });

  it("closing the tab sends the pause the server never received", async () => {
    const { controller, uploaderOptions } = await startCapture();
    await stallWithServerUnreachable(uploaderOptions);
    vi.mocked(sendPauseBeacon).mockClear();

    await (controller as unknown as {
      handleGuardedExit: (request: { reason: string; useBeacon: boolean }) => Promise<void>;
    }).handleGuardedExit({ reason: "pagehide", useBeacon: true });

    expect(sendPauseBeacon).toHaveBeenCalledWith("rec-1", 0);
  });

  it("a stop that fails pauses the capture on the server, so it is offered rather than read as live", async () => {
    const { controller } = await startCapture();
    apiMocks.finalizeRecordingCapture.mockRejectedValue(unreachable());
    apiMocks.getUnfinishedCaptures.mockResolvedValue([
      { id: "rec-1", name: "Weekly sync", status: "PAUSED", client_status: "PAUSED" },
    ]);

    await expect(controller.stop()).rejects.toThrow("Network Error");

    await vi.waitFor(() =>
      expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledWith("rec-1", { epoch: 0 }),
    );
    await vi.waitFor(() =>
      expect(controller.getState().pausedRecording).toMatchObject({ id: "rec-1" }),
    );
    expect(controller.getState().status).toBe("paused");
    expect(controller.getState().runtimeActive).toBe(false);
  });
});

describe("a capture taken over or stopped elsewhere (issues #570, #590)", () => {
  const microphoneSources = () => ({
    mode: "microphone_only",
    displayStream: null,
    microphoneStream: {} as MediaStream,
    captureReport: {
      mode: "microphone_only",
      requested_microphone_device_id: null,
      requested_microphone_label: null,
      available_microphones: [],
      browser_microphone_track: null,
      browser_display_audio_track: null,
      browser_display_video_track: null,
      shared_audio_available: false,
      notes: [],
    },
    release: vi.fn(),
  });

  /** The server's answer to a request from a tab whose capture moved on. */
  const superseded = () => {
    const error = new AxiosError("Request failed with status code 409");
    Object.assign(error, {
      response: {
        status: 409,
        data: {
          detail: {
            code: "capture_superseded",
            message: "This recording continued elsewhere: another tab or device resumed it.",
          },
        },
      },
    });
    return error;
  };

  /** The server's answer once the recording was stopped, which issues no epoch. */
  const closed = () => {
    const error = new AxiosError("Request failed with status code 409");
    Object.assign(error, {
      response: {
        status: 409,
        data: { detail: "Recording is no longer accepting capture uploads" },
      },
    });
    return error;
  };

  const STOPPED = /already been stopped and sent for processing/;

  const unreachable = () => new AxiosError("Network Error", "ERR_NETWORK");

  const stored = (sequence: number, epoch?: number) => ({
    recordingId: "rec-1",
    sequence,
    blob: new Blob([String(sequence)], { type: "audio/flac" }),
    durationSeconds: 2,
    ...(epoch === undefined ? {} : { epoch }),
  });

  const lifecycle = (status: string, lastSequence: number, epoch: number) => ({
    recording_id: "rec-1",
    status,
    last_sequence: lastSequence,
    capture_epoch: epoch,
  });

  const controllers: CaptureController[] = [];

  const track = (controller: CaptureController) => {
    controllers.push(controller);
    return controller;
  };

  /** The controller's own store, which a test fills as an earlier tab would have. */
  const storeOf = (controller: CaptureController) =>
    (controller as unknown as { segmentStore: SegmentStore }).segmentStore;

  const startCapture = async (epoch = 3) => {
    const sources = microphoneSources();
    pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);
    apiMocks.initRecording.mockResolvedValue({
      id: "rec-1",
      name: "Weekly sync",
      capture_epoch: epoch,
    });
    const controller = track(new CaptureController());
    await controller.start("Weekly sync");
    return {
      controller,
      sources,
      uploaderOptions: vi.mocked(createSegmentUploader).mock.calls[0][0],
      uploader: vi.mocked(createSegmentUploader).mock.results[0].value,
      recorder: vi.mocked(createBrowserRecorder).mock.results[0].value,
    };
  };

  type Started = Awaited<ReturnType<typeof startCapture>>;

  /** The tab has stopped capturing and holds nothing of the recording any more. */
  const expectStoodDown = (
    { controller, sources, uploader, recorder }: Started,
    message = /continued in another tab or on another device/,
  ) => {
    const state = controller.getState();
    expect(state.status).toBe("idle");
    expect(state.recordingId).toBeNull();
    expect(state.runtimeActive).toBe(false);
    expect(state.error).toMatch(message);
    expect(recorder.stop).toHaveBeenCalledWith({ emitTail: false });
    expect(sources.release).toHaveBeenCalled();
    expect(uploader.dispose).toHaveBeenCalled();
    expect(captureLockMocks.release).toHaveBeenCalled();
  };

  beforeEach(() => {
    resetSessionForTests();
    vi.mocked(createSegmentUploader).mockClear();
    vi.mocked(createBrowserRecorder).mockClear();
    apiMocks.initRecording.mockReset();
    apiMocks.pauseRecordingCapture.mockReset();
    apiMocks.pauseRecordingCapture.mockResolvedValue(lifecycle("PAUSED", 4, 3));
    apiMocks.resumeRecordingCapture.mockReset();
    apiMocks.resumeRecordingCapture.mockResolvedValue(lifecycle("UPLOADING", 4, 3));
    apiMocks.finalizeRecordingCapture.mockReset();
    apiMocks.finalizeRecordingCapture.mockResolvedValue({ id: "rec-1", status: "QUEUED" });
    apiMocks.discardRecordingCapture.mockReset();
    apiMocks.discardRecordingCapture.mockResolvedValue(undefined);
    apiMocks.getUnfinishedCaptures.mockReset();
    apiMocks.getUnfinishedCaptures.mockResolvedValue([]);
    apiMocks.reportRecordingCaptureSources.mockResolvedValue(undefined);
    pickSourceMocks.pickCaptureSource.mockReset();
    notificationMocks.addNotification.mockReset();
    featureDetectMocks.detectCaptureSupport.mockReturnValue({
      supported: true,
      mode: "microphone_only",
    });
    sharedMocks.readPausedCaptureContext.mockReset();
    sharedMocks.readPausedCaptureContext.mockReturnValue(null);
    connectivityMocks.status = "online";
    connectivityMocks.lastReachableAt = null;
    connectivityMocks.listeners.clear();
    captureLockMocks.release.mockClear();
  });

  afterEach(async () => {
    await Promise.all(controllers.splice(0).map((controller) => controller.destroy()));
    resetSessionForTests();
  });

  it("sends the epoch it was issued with every request for its capture", async () => {
    const { controller, uploaderOptions } = await startCapture(3);
    expect(uploaderOptions.epoch).toBe(3);

    await controller.pause();
    await controller.resume();
    await controller.stop();

    expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledWith("rec-1", { epoch: 3 });
    expect(apiMocks.resumeRecordingCapture).toHaveBeenCalledWith("rec-1", { epoch: 3 });
    expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalledWith(
      "rec-1",
      expect.objectContaining({ epoch: 3 }),
    );
  });

  it("stops capturing once a segment is refused, keeping the unsent audio in the browser", async () => {
    const started = await startCapture();
    vi.mocked(started.uploader.pendingAudioSeconds).mockReturnValue(6);
    const clearStore = vi.spyOn(storeOf(started.controller), "clear");

    await started.uploaderOptions.onCaptureEnded?.(superseded());

    expectStoodDown(started);
    expect(started.controller.getState().error).toMatch(/About 6 seconds of audio/);
    // Nothing more is asked of the server for a capture that is not this tab's.
    expect(clearStore).not.toHaveBeenCalled();
    expect(apiMocks.pauseRecordingCapture).not.toHaveBeenCalled();
    expect(apiMocks.finalizeRecordingCapture).not.toHaveBeenCalled();
    expect(apiMocks.discardRecordingCapture).not.toHaveBeenCalled();
  });

  it("stands down when its pause is refused, rather than reading paused", async () => {
    const started = await startCapture();
    apiMocks.pauseRecordingCapture.mockRejectedValueOnce(superseded());

    await expect(started.controller.pause()).rejects.toThrow(/continued in another tab/);

    expectStoodDown(started);
  });

  it("stands down when its resume is refused", async () => {
    const started = await startCapture();
    await started.controller.pause();
    apiMocks.resumeRecordingCapture.mockRejectedValueOnce(superseded());

    await expect(started.controller.resume()).rejects.toThrow(/continued in another tab/);

    expectStoodDown(started);
    expect(started.recorder.resume).not.toHaveBeenCalled();
  });

  it("neither finalizes nor owes a pause when its stop is refused", async () => {
    const started = await startCapture();
    apiMocks.finalizeRecordingCapture.mockRejectedValueOnce(superseded());

    await expect(started.controller.stop()).rejects.toThrow(/continued in another tab/);

    expectStoodDown(started);
    connectivityMocks.apiAnswered();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(apiMocks.pauseRecordingCapture).not.toHaveBeenCalled();
    expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalledTimes(1);
  });

  it("does not finalize when the queue it flushes on stop is refused", async () => {
    const started = await startCapture();
    vi.mocked(started.uploader.waitForIdle).mockRejectedValueOnce(superseded());

    await expect(started.controller.stop()).rejects.toThrow(/continued in another tab/);

    expect(apiMocks.finalizeRecordingCapture).not.toHaveBeenCalled();
    expectStoodDown(started);
  });

  it("stands down when the pause it owed is refused once the API answers", async () => {
    const started = await startCapture();
    apiMocks.pauseRecordingCapture.mockRejectedValueOnce(unreachable());
    await started.uploaderOptions.onStalled?.(unreachable());
    await vi.waitFor(() => expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(started.controller.getState().status).toBe("paused");

    apiMocks.pauseRecordingCapture.mockRejectedValueOnce(superseded());
    connectivityMocks.apiAnswered();

    await vi.waitFor(() => expect(started.controller.getState().status).toBe("idle"));
    expectStoodDown(started);
    expect(apiMocks.pauseRecordingCapture).toHaveBeenLastCalledWith("rec-1", { epoch: 3 });
  });

  it("leaves the recording to the tab running it when its discard is refused", async () => {
    const started = await startCapture();
    apiMocks.discardRecordingCapture.mockRejectedValueOnce(superseded());
    const clearStore = vi.spyOn(storeOf(started.controller), "clear");

    await expect(started.controller.cancel()).rejects.toThrow(/continued in another tab/);

    expect(apiMocks.discardRecordingCapture).toHaveBeenCalledWith("rec-1", undefined, {
      epoch: 3,
    });
    expect(clearStore).not.toHaveBeenCalled();
    expect(started.controller.getState().status).toBe("idle");
  });

  it("leaves out audio it kept from before another device carried the capture on", async () => {
    const controller = track(new CaptureController());
    // Kept under epoch 1. Another device took over at 2 and recorded 5 and 6,
    // so this browser's takeover is issued 3, after sequence 6.
    for (const sequence of [5, 6, 7, 8]) {
      await storeOf(controller).put(stored(sequence, 1));
    }
    pickSourceMocks.pickCaptureSource.mockResolvedValue(microphoneSources());
    apiMocks.resumeRecordingCapture.mockResolvedValue(lifecycle("UPLOADING", 6, 3));

    await controller.resume("rec-1");

    expect(apiMocks.resumeRecordingCapture).toHaveBeenCalledWith("rec-1", { epoch: undefined });
    expect(vi.mocked(createSegmentUploader).mock.calls[0][0].epoch).toBe(3);
    const uploader = vi.mocked(createSegmentUploader).mock.results[0].value;
    // 7 and 8 would otherwise follow the other device's audio out of order.
    expect(uploader.restore).not.toHaveBeenCalled();
    expect(vi.mocked(createBrowserRecorder).mock.calls[0][0].startSequence).toBe(7);
    await vi.waitFor(async () => expect(await storeOf(controller).list("rec-1")).toEqual([]));
    expect(notificationMocks.addNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "warning",
        message: expect.stringMatching(/^About 8 seconds of audio this browser kept/),
      }),
    );
  });

  it("still sends audio kept under the epoch just before its takeover", async () => {
    const controller = track(new CaptureController());
    for (const sequence of [5, 6]) {
      await storeOf(controller).put(stored(sequence, 2));
    }
    pickSourceMocks.pickCaptureSource.mockResolvedValue(microphoneSources());
    apiMocks.resumeRecordingCapture.mockResolvedValue(lifecycle("UPLOADING", 4, 3));

    await controller.resume("rec-1");

    const uploader = vi.mocked(createSegmentUploader).mock.results[0].value;
    expect(
      uploader.restore.mock.calls[0][0].map((segment: { sequence: number }) => segment.sequence),
    ).toEqual([5, 6]);
    expect(vi.mocked(createBrowserRecorder).mock.calls[0][0].startSequence).toBe(7);
    expect(notificationMocks.addNotification).not.toHaveBeenCalled();
  });

  it("sends on stop only the audio kept under the epoch the server holds", async () => {
    const controller = track(new CaptureController());
    for (const sequence of [5, 6]) {
      await storeOf(controller).put(stored(sequence, 1));
    }
    apiMocks.pauseRecordingCapture.mockResolvedValue(lifecycle("PAUSED", 4, 2));

    await controller.stop("rec-1");

    expect(createSegmentUploader).not.toHaveBeenCalled();
    expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalledTimes(1);
    expect(notificationMocks.addNotification).toHaveBeenCalledWith(
      expect.objectContaining({ type: "warning" }),
    );
  });

  it("resumes as the tab holding the capture after a stop that failed", async () => {
    const started = await startCapture(3);
    apiMocks.finalizeRecordingCapture.mockRejectedValueOnce(unreachable());
    await expect(started.controller.stop()).rejects.toThrow("Network Error");
    await storeOf(started.controller).put(stored(5, 3));
    pickSourceMocks.pickCaptureSource.mockResolvedValue(microphoneSources());

    await started.controller.resume();

    expect(apiMocks.resumeRecordingCapture).toHaveBeenCalledWith("rec-1", { epoch: 3 });
    expect(vi.mocked(createSegmentUploader).mock.calls[1][0].epoch).toBe(3);
    const second = vi.mocked(createSegmentUploader).mock.results[1].value;
    expect(
      second.restore.mock.calls[0][0].map((segment: { sequence: number }) => segment.sequence),
    ).toEqual([5]);
    expect(started.controller.getState().status).toBe("recording");
  });

  describe("once the recording was discarded elsewhere (issue #602)", () => {
    const ALICE = 7;
    const DISCARDED = /discarded in another tab or on another device/;

    const notFound = () => {
      const error = new AxiosError("Request failed with status code 404");
      Object.assign(error, { response: { status: 404, data: { detail: "Recording not found" } } });
      return error;
    };

    const signedInAs = (id: number, username: string) => ({
      id,
      username,
      force_password_change: false,
    });

    beforeEach(() => {
      // The capture belongs to Alice, the account the page was signed in as.
      noteSignedIn(signedInAs(ALICE, "alice"));
      apiMocks.getUserMe.mockReset();
      apiMocks.getUserMe.mockResolvedValue(signedInAs(ALICE, "alice"));
      apiMocks.getRecording.mockReset();
      apiMocks.getRecording.mockRejectedValue(notFound());
    });

    it("stands down at the first 404 for a segment, once /users/me names its owner", async () => {
      const started = await startCapture();

      const gone = await started.uploaderOptions.checkCaptureGone?.(notFound());
      expect(gone).toBeInstanceOf(Error);
      await started.uploaderOptions.onCaptureEnded?.(gone as Error);

      expectStoodDown(started, DISCARDED);
      // The 404 and /users/me went out apart, so the recording was asked again.
      expect(apiMocks.getRecording).toHaveBeenCalledWith("rec-1");
    });

    it("waits for the owner when the 404 came under another account's sign-in", async () => {
      const started = await startCapture();
      apiMocks.getUserMe.mockResolvedValue(signedInAs(8, "bob"));

      await expect(started.uploaderOptions.checkCaptureGone?.(notFound())).resolves.toBeNull();

      // The held sign-in dialog opens for Alice, and uploads wait for her.
      expect(useSessionStore.getState()).toMatchObject({
        signInRequired: true,
        otherUsername: "bob",
      });
      expect(started.controller.getState().status).toBe("recording");
      expect(apiMocks.getRecording).not.toHaveBeenCalled();
    });

    it("carries on when the recording answers again once the owner is confirmed", async () => {
      const started = await startCapture();
      apiMocks.getRecording.mockResolvedValue({ id: "rec-1", status: "UPLOADING" });

      await expect(started.uploaderOptions.checkCaptureGone?.(notFound())).resolves.toBeNull();
      expect(started.controller.getState().status).toBe("recording");
    });

    it("carries on when it cannot tell who is signed in", async () => {
      const started = await startCapture();
      apiMocks.getUserMe.mockRejectedValue(new AxiosError("Network Error", "ERR_NETWORK"));

      await expect(started.uploaderOptions.checkCaptureGone?.(notFound())).resolves.toBeNull();
      expect(started.controller.getState().status).toBe("recording");
    });

    it("stands down when its pause answers 404", async () => {
      const started = await startCapture();
      apiMocks.pauseRecordingCapture.mockRejectedValueOnce(notFound());

      await expect(started.controller.pause()).rejects.toThrow(DISCARDED);

      expectStoodDown(started, DISCARDED);
    });

    it("stands down when its resume answers 404", async () => {
      const started = await startCapture();
      await started.controller.pause();
      apiMocks.resumeRecordingCapture.mockRejectedValueOnce(notFound());

      await expect(started.controller.resume()).rejects.toThrow(DISCARDED);

      expectStoodDown(started, DISCARDED);
      expect(started.recorder.resume).not.toHaveBeenCalled();
    });

    it("stands down rather than reading paused when its stop answers 404", async () => {
      const started = await startCapture();
      apiMocks.finalizeRecordingCapture.mockRejectedValueOnce(notFound());

      await expect(started.controller.stop()).rejects.toThrow(DISCARDED);

      expectStoodDown(started, DISCARDED);
      connectivityMocks.apiAnswered();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(apiMocks.pauseRecordingCapture).not.toHaveBeenCalled();
    });

    it("reads idle with no error when its discard finds it already discarded", async () => {
      const started = await startCapture();
      apiMocks.discardRecordingCapture.mockRejectedValueOnce(notFound());

      await expect(started.controller.cancel()).resolves.toBeUndefined();

      const state = started.controller.getState();
      expect(state.status).toBe("idle");
      expect(state.recordingId).toBeNull();
      expect(state.error).toBeNull();
    });

    it("pauses once the owner signs back in, when its pause answered 404 under another account", async () => {
      const started = await startCapture();
      apiMocks.getUserMe.mockResolvedValue(signedInAs(8, "bob"));
      apiMocks.pauseRecordingCapture.mockRejectedValueOnce(notFound());

      const pausing = started.controller.pause();
      await vi.waitFor(() => expect(useSessionStore.getState().signInRequired).toBe(true));
      expect(started.controller.getState().status).toBe("paused");

      noteSignedIn(signedInAs(ALICE, "alice"));
      await pausing;

      expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(2);
      expect(started.controller.getState().status).toBe("paused");
      expect(started.controller.getState().error).toBeNull();
      expect(started.uploader.dispose).not.toHaveBeenCalled();
    });
  });

  describe("once the recording was stopped elsewhere (issue #590)", () => {
    it("stops capturing at the first refused segment", async () => {
      const started = await startCapture();
      vi.mocked(started.uploader.pendingAudioSeconds).mockReturnValue(4);

      await started.uploaderOptions.onCaptureEnded?.(closed());

      expectStoodDown(started, STOPPED);
      expect(started.controller.getState().error).toMatch(/About 4 seconds of audio/);
      expect(apiMocks.pauseRecordingCapture).not.toHaveBeenCalled();
      expect(apiMocks.finalizeRecordingCapture).not.toHaveBeenCalled();
    });

    it("then removes the audio it kept, which can no longer join the recording (issue #589)", async () => {
      const started = await startCapture();
      await storeOf(started.controller).put(stored(5, 3));
      apiMocks.getRecording.mockResolvedValue({ id: "rec-1", status: "QUEUED" });

      await started.uploaderOptions.onCaptureEnded?.(closed());

      expectStoodDown(started, STOPPED);
      await vi.waitFor(async () =>
        expect(await storeOf(started.controller).list("rec-1")).toEqual([]),
      );
    });

    it("stands down when the pause it owed is refused once the API answers", async () => {
      const started = await startCapture();
      apiMocks.pauseRecordingCapture.mockRejectedValueOnce(unreachable());
      await started.uploaderOptions.onStalled?.(unreachable());
      await vi.waitFor(() => expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(1));
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(started.controller.getState().status).toBe("paused");

      apiMocks.pauseRecordingCapture.mockRejectedValueOnce(closed());
      connectivityMocks.apiAnswered();

      await vi.waitFor(() => expect(started.controller.getState().status).toBe("idle"));
      expectStoodDown(started, STOPPED);
    });

    it("stands down when its pause is refused", async () => {
      const started = await startCapture();
      apiMocks.pauseRecordingCapture.mockRejectedValueOnce(closed());

      await expect(started.controller.pause()).rejects.toThrow(STOPPED);

      expectStoodDown(started, STOPPED);
    });

    it("stands down when its resume is refused", async () => {
      const started = await startCapture();
      await started.controller.pause();
      apiMocks.resumeRecordingCapture.mockRejectedValueOnce(closed());

      await expect(started.controller.resume()).rejects.toThrow(STOPPED);

      expectStoodDown(started, STOPPED);
      expect(started.recorder.resume).not.toHaveBeenCalled();
    });

    it("stands down rather than reading paused when its stop finds it already stopped", async () => {
      const started = await startCapture();
      apiMocks.finalizeRecordingCapture.mockRejectedValueOnce(closed());

      await expect(started.controller.stop()).rejects.toThrow(STOPPED);

      expectStoodDown(started, STOPPED);
      // Nothing is owed to a recording that takes no more requests.
      connectivityMocks.apiAnswered();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(apiMocks.pauseRecordingCapture).not.toHaveBeenCalled();
    });

    it("reads idle when a stop from the dialog finds it already stopped", async () => {
      const controller = track(new CaptureController());
      apiMocks.finalizeRecordingCapture.mockRejectedValueOnce(closed());

      await expect(controller.stop("rec-1")).rejects.toThrow(STOPPED);

      expect(controller.getState().status).toBe("idle");
      expect(controller.getState().recordingId).toBeNull();
    });

    it("leaves the recording to processing when its discard is refused", async () => {
      const started = await startCapture();
      apiMocks.discardRecordingCapture.mockRejectedValueOnce(closed());
      const clearStore = vi.spyOn(storeOf(started.controller), "clear");

      await expect(started.controller.cancel()).rejects.toThrow(STOPPED);

      expect(apiMocks.discardRecordingCapture).toHaveBeenCalledWith("rec-1", undefined, {
        epoch: 3,
      });
      expect(clearStore).not.toHaveBeenCalled();
      expect(started.controller.getState().status).toBe("idle");
    });
  });

  describe("keeping the audio it left out to download (issue #605)", () => {
    const ALICE = 7;

    /** A real 2 s capture segment, whose samples differ by sequence. */
    const pcmFor = (sequence: number) => {
      const pcm = new Int16Array(32_000 * 2);
      for (let frame = 0; frame < 32_000; frame += 1) {
        pcm[frame * 2] = Math.round(6000 * Math.sin((frame + sequence * 977) / 13));
        pcm[frame * 2 + 1] = sequence * 10;
      }
      return pcm;
    };

    const flacSegment = (sequence: number, epoch: number) => ({
      recordingId: "rec-1",
      sequence,
      blob: new Blob([encodeFlac(pcmFor(sequence), 2, 16_000).buffer as ArrayBuffer], {
        type: "audio/flac",
      }),
      durationSeconds: 2,
      epoch,
      ownerId: ALICE,
    });

    const leftOutOf = (controller: CaptureController) =>
      (controller as unknown as { leftOutAudio: LeftOutAudioStore }).leftOutAudio;

    const sequencesKept = async (controller: CaptureController) =>
      (await storeOf(controller).list("rec-1")).map((segment) => segment.sequence);

    /** The samples of one joined download. */
    const samplesOf = async (audio: LeftOutAudio) =>
      decodeFlac(new Uint8Array(await audio.blob.arrayBuffer())).samples;

    const joined = (sequences: number[]) => {
      const all = new Int16Array(sequences.length * 32_000 * 2);
      sequences.forEach((sequence, index) => all.set(pcmFor(sequence), index * 32_000 * 2));
      return all;
    };

    const offered = () =>
      notificationMocks.addNotification.mock.calls
        .map(([notification]) => notification)
        .filter((notification) => String(notification.id).startsWith("left-out-audio-"));

    beforeEach(() => {
      apiMocks.getUserMe.mockReset();
      apiMocks.getUserMe.mockResolvedValue({ id: ALICE, username: "alice" });
      apiMocks.getRecording.mockReset();
      apiMocks.getRecording.mockResolvedValue({
        id: "rec-1",
        name: "Weekly sync",
        status: "UPLOADING",
      });
    });

    it.each([
      ["continues elsewhere", superseded],
      ["is stopped elsewhere", closed],
    ])("keeps the audio a tab had not uploaded when its capture %s", async (_name, refusal) => {
      const started = await startCapture(3);
      for (const sequence of [5, 6]) {
        await storeOf(started.controller).put(flacSegment(sequence, 3));
      }
      // Kept by an earlier tab of this browser: not this one's to move.
      await storeOf(started.controller).put(flacSegment(2, 1));

      await started.uploaderOptions.onCaptureEnded?.(refusal());

      await vi.waitFor(async () => expect(await leftOutOf(started.controller).list()).toHaveLength(1));
      const [audio] = await leftOutOf(started.controller).list();
      expect(audio).toMatchObject({
        recordingId: "rec-1",
        recordingName: "Weekly sync",
        ownerId: ALICE,
        seconds: 4,
      });
      expect(await samplesOf(audio)).toEqual(joined([5, 6]));
      expect(await sequencesKept(started.controller)).toEqual([2]);
      await vi.waitFor(() =>
        expect(offered()).toEqual([
          expect.objectContaining({ id: `left-out-audio-${audio.id}`, persistent: true }),
        ]),
      );
    });

    it("waits for the segments the tab was still storing", async () => {
      const started = await startCapture(3);
      let storeLate: () => void = () => {};
      vi.mocked(started.uploader.settled).mockImplementationOnce(
        () =>
          new Promise<void>((resolve) => {
            storeLate = () => {
              void storeOf(started.controller).put(flacSegment(5, 3)).then(resolve);
            };
          }),
      );

      await started.uploaderOptions.onCaptureEnded?.(superseded());
      storeLate();

      await vi.waitFor(async () => expect(await leftOutOf(started.controller).list()).toHaveLength(1));
      expect(await sequencesKept(started.controller)).toEqual([]);
    });

    it("keeps the audio a takeover in this browser leaves out", async () => {
      const controller = track(new CaptureController());
      for (const sequence of [5, 6, 7, 8]) {
        await storeOf(controller).put(flacSegment(sequence, 1));
      }
      (controller as unknown as { state: object }).state = {
        ...controller.getState(),
        pausedRecording: { id: "rec-1", name: "Weekly sync" },
      };
      pickSourceMocks.pickCaptureSource.mockResolvedValue(microphoneSources());
      apiMocks.resumeRecordingCapture.mockResolvedValue(lifecycle("UPLOADING", 6, 3));
      let keptWhenRecordingBegan: number[] | null = null;
      vi.mocked(createSegmentUploader).mockImplementationOnce((options) => {
        void storeOf(controller)
          .list("rec-1")
          .then((segments) => {
            keptWhenRecordingBegan = segments.map((segment) => segment.sequence);
          });
        return {
          enqueue: vi.fn(),
          restore: vi.fn(),
          waitForIdle: vi.fn(),
          recover: vi.fn(() => true),
          dispose: vi.fn(),
          settled: vi.fn(async () => {}),
          pendingSegmentCount: vi.fn(() => 0),
          pendingAudioSeconds: vi.fn(() => 0),
          options,
        } as never;
      });

      await controller.resume("rec-1");

      const [audio] = await leftOutOf(controller).list();
      expect(audio).toMatchObject({ recordingName: "Weekly sync", seconds: 8 });
      expect(await samplesOf(audio)).toEqual(joined([5, 6, 7, 8]));
      await vi.waitFor(() => expect(keptWhenRecordingBegan).toEqual([]));
      // Offered for download, so the warning that it was lost is not given.
      expect(notificationMocks.addNotification).not.toHaveBeenCalledWith(
        expect.objectContaining({ message: expect.stringMatching(/was left out, because/) }),
      );
      // Offered again by each check, which the notification store ignores
      // while the first is on screen.
      await vi.waitFor(() =>
        expect(new Set(offered().map((notification) => notification.id))).toEqual(
          new Set([`left-out-audio-${audio.id}`]),
        ),
      );
    });

    it("keeps nothing of a recording discarded elsewhere", async () => {
      noteSignedIn({ username: "alice", force_password_change: false, id: ALICE });
      const started = await startCapture(3);
      await storeOf(started.controller).put(flacSegment(5, 3));
      const notFound = new AxiosError("Request failed with status code 404");
      Object.assign(notFound, { response: { status: 404, data: { detail: "Recording not found" } } });
      apiMocks.getRecording.mockRejectedValue(notFound);

      const gone = await started.uploaderOptions.checkCaptureGone?.(notFound);
      await started.uploaderOptions.onCaptureEnded?.(gone as Error);

      // The check for finished captures removes it, as the discard asked.
      await vi.waitFor(async () => expect(await sequencesKept(started.controller)).toEqual([]));
      expect(await leftOutOf(started.controller).list()).toEqual([]);
      expect(offered()).toEqual([]);
    });
  });
});

describe("a pause from the controls that the server does not take (issue #582)", () => {
  const microphoneSources = () => ({
    mode: "microphone_only",
    displayStream: null,
    microphoneStream: {} as MediaStream,
    captureReport: {
      mode: "microphone_only",
      requested_microphone_device_id: null,
      requested_microphone_label: null,
      available_microphones: [],
      browser_microphone_track: null,
      browser_display_audio_track: null,
      browser_display_video_track: null,
      shared_audio_available: false,
      notes: [],
    },
    release: vi.fn(),
  });

  const unreachable = () => new AxiosError("Network Error", "ERR_NETWORK");

  /** An answer from the API itself, which asking again would only repeat. */
  const serverError = () => {
    const error = new AxiosError("Request failed with status code 500");
    Object.assign(error, {
      response: { status: 500, data: { detail: "The pause could not be saved." } },
    });
    return error;
  };

  const paused = (lastSequence: number) => ({
    recording_id: "rec-1",
    status: "PAUSED",
    last_sequence: lastSequence,
    capture_epoch: 2,
  });

  const controllers: CaptureController[] = [];

  const startCapture = async () => {
    const sources = microphoneSources();
    pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);
    apiMocks.initRecording.mockResolvedValue({
      id: "rec-1",
      name: "Weekly sync",
      capture_epoch: 2,
    });
    const controller = new CaptureController();
    controllers.push(controller);
    await controller.start("Weekly sync");
    return {
      controller,
      sources,
      uploader: vi.mocked(createSegmentUploader).mock.results[0].value,
      recorder: vi.mocked(createBrowserRecorder).mock.results[0].value,
    };
  };

  beforeEach(() => {
    resetSessionForTests();
    vi.mocked(createSegmentUploader).mockClear();
    vi.mocked(createBrowserRecorder).mockClear();
    apiMocks.initRecording.mockReset();
    apiMocks.pauseRecordingCapture.mockReset();
    apiMocks.pauseRecordingCapture.mockResolvedValue(paused(7));
    apiMocks.resumeRecordingCapture.mockReset();
    apiMocks.resumeRecordingCapture.mockResolvedValue({
      recording_id: "rec-1",
      status: "UPLOADING",
      last_sequence: 7,
      capture_epoch: 2,
    });
    apiMocks.finalizeRecordingCapture.mockReset();
    apiMocks.finalizeRecordingCapture.mockResolvedValue({ id: "rec-1", status: "QUEUED" });
    apiMocks.getUnfinishedCaptures.mockReset();
    apiMocks.getUnfinishedCaptures.mockResolvedValue([]);
    apiMocks.reportRecordingCaptureSources.mockResolvedValue(undefined);
    pickSourceMocks.pickCaptureSource.mockReset();
    notificationMocks.addNotification.mockReset();
    featureDetectMocks.detectCaptureSupport.mockReturnValue({
      supported: true,
      mode: "microphone_only",
    });
    sharedMocks.readPausedCaptureContext.mockReset();
    sharedMocks.readPausedCaptureContext.mockReturnValue(null);
    connectivityMocks.status = "online";
    connectivityMocks.lastReachableAt = null;
    connectivityMocks.listeners.clear();
    captureLockMocks.release.mockClear();
  });

  afterEach(async () => {
    await Promise.all(controllers.splice(0).map((controller) => controller.destroy()));
    vi.useRealTimers();
    resetSessionForTests();
  });

  it("reads paused, and owes the server the pause, when the request does not arrive", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    const { controller, sources, uploader, recorder } = await startCapture();
    await vi.advanceTimersByTimeAsync(20_000);
    apiMocks.pauseRecordingCapture.mockRejectedValueOnce(unreachable());

    await controller.pause();

    const state = controller.getState();
    expect(recorder.pause).toHaveBeenCalledTimes(1);
    // Never "recording" while the recorder takes no audio.
    expect(state.status).toBe("paused");
    expect(state.runtimeActive).toBe(true);
    expect(sources.release).not.toHaveBeenCalled();
    expect(uploader.dispose).not.toHaveBeenCalled();
    expect(captureLockMocks.release).not.toHaveBeenCalled();
    expect(notificationMocks.addNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "warning",
        message: expect.stringMatching(/^Recording paused\. Nojoin could not reach the server/),
      }),
    );
    const elapsed = state.elapsedSeconds;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(controller.getState().elapsedSeconds).toBe(elapsed);

    connectivityMocks.apiAnswered();

    await vi.waitFor(() => expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(2));
    expect(apiMocks.pauseRecordingCapture).toHaveBeenLastCalledWith("rec-1", { epoch: 2 });
    await vi.waitFor(() => expect(controller.getState().lastSequence).toBe(7));
    expect(controller.getState().status).toBe("paused");
  });

  it("sends the pause it owes before resuming the same capture", async () => {
    const { controller, recorder } = await startCapture();
    apiMocks.pauseRecordingCapture.mockRejectedValueOnce(unreachable());
    await controller.pause();
    const order: string[] = [];
    apiMocks.pauseRecordingCapture.mockImplementation(async () => {
      order.push("pause");
      return paused(7);
    });
    apiMocks.resumeRecordingCapture.mockImplementation(async () => {
      order.push("resume");
      return { recording_id: "rec-1", status: "UPLOADING", last_sequence: 7, capture_epoch: 2 };
    });

    await expect(controller.resume()).resolves.toEqual({ recordingId: "rec-1", resumed: true });

    expect(order).toEqual(["pause", "resume"]);
    expect(recorder.resume).toHaveBeenCalledTimes(1);
    expect(pickSourceMocks.pickCaptureSource).toHaveBeenCalledTimes(1);
    expect(controller.getState().status).toBe("recording");
  });

  it("stays paused with the server's reason when the server refuses the pause", async () => {
    const { controller } = await startCapture();
    apiMocks.pauseRecordingCapture.mockRejectedValueOnce(serverError());

    await expect(controller.pause()).rejects.toThrow("The pause could not be saved.");

    expect(controller.getState().status).toBe("paused");
    expect(controller.getState().runtimeActive).toBe(true);
    // A refusal is the server's answer, so it is not asked again.
    connectivityMocks.apiAnswered();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(1);
  });

  it("stays paused when the queued audio cannot be sent before the pause", async () => {
    const { controller, uploader } = await startCapture();
    vi.mocked(uploader.waitForIdle).mockRejectedValueOnce(
      new Error("Timed out waiting for recording segment 4 to upload."),
    );

    await controller.pause();

    // The pause still goes, and the queue stays for resume or stop to send.
    expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledWith("rec-1", { epoch: 2 });
    expect(uploader.dispose).not.toHaveBeenCalled();
    expect(controller.getState().status).toBe("paused");
  });

  it("reads paused while the queued audio uploads, before the server is told", async () => {
    const { controller, uploader } = await startCapture();
    let uploadsDone = () => {};
    vi.mocked(uploader.waitForIdle).mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          uploadsDone = resolve;
        }),
    );

    const pausing = controller.pause();
    await vi.waitFor(() => expect(controller.getState().status).toBe("paused"));
    expect(apiMocks.pauseRecordingCapture).not.toHaveBeenCalled();

    uploadsDone();
    await pausing;

    expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(1);
    expect(controller.getState().status).toBe("paused");
  });

  it("a stop pressed while the pause is on its way is not undone by its answer", async () => {
    const { controller } = await startCapture();
    let answerPause = () => {};
    apiMocks.pauseRecordingCapture.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answerPause = () => resolve(paused(7));
        }),
    );

    const pausing = controller.pause();
    await vi.waitFor(() => expect(apiMocks.pauseRecordingCapture).toHaveBeenCalledTimes(1));
    await controller.stop();
    expect(controller.getState().status).toBe("idle");

    answerPause();
    await pausing;

    expect(controller.getState().status).toBe("idle");
    expect(controller.getState().recordingId).toBeNull();
  });
});

describe("audio kept for a capture finished elsewhere (issue #589)", () => {
  /** The account the page is signed in as in these tests. */
  const ALICE = 7;

  const kept = (recordingId: string, sequence: number, ownerId?: number) => ({
    recordingId,
    sequence,
    blob: new Blob([String(sequence)], { type: "audio/flac" }),
    durationSeconds: 2,
    epoch: 0,
    ...(ownerId === undefined ? {} : { ownerId }),
  });

  const notFound = () => {
    const error = new AxiosError("Request failed with status code 404");
    Object.assign(error, { response: { status: 404, data: { detail: "Recording not found" } } });
    return error;
  };

  const controllers: CaptureController[] = [];

  const storeOf = (controller: CaptureController) =>
    (controller as unknown as { segmentStore: SegmentStore }).segmentStore;

  /** A controller whose browser kept two segments of `recordingId`. */
  const keeping = async (recordingId: string, ownerId?: number) => {
    const controller = new CaptureController();
    controllers.push(controller);
    await storeOf(controller).put(kept(recordingId, 4, ownerId));
    await storeOf(controller).put(kept(recordingId, 5, ownerId));
    return controller;
  };

  const keptSequences = async (controller: CaptureController, recordingId: string) =>
    (await storeOf(controller).list(recordingId)).map((segment) => segment.sequence);

  /** Lets the check a refresh starts run to the end. */
  const settle = async (controller: CaptureController) => {
    await vi.waitFor(() =>
      expect((controller as unknown as { checkingKeptAudio: boolean }).checkingKeptAudio).toBe(
        false,
      ),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
  };

  beforeEach(() => {
    resetSessionForTests();
    noteSignedIn({ username: "alice", force_password_change: false, id: ALICE });
    apiMocks.getUnfinishedCaptures.mockReset();
    apiMocks.getUnfinishedCaptures.mockResolvedValue([]);
    apiMocks.getRecording.mockReset();
    apiMocks.getUserMe.mockReset();
    apiMocks.getUserMe.mockResolvedValue({ id: ALICE, username: "alice" });
    sharedMocks.readPausedCaptureContext.mockReset();
    sharedMocks.readPausedCaptureContext.mockReturnValue(null);
  });

  afterEach(async () => {
    await Promise.all(controllers.splice(0).map((controller) => controller.destroy()));
    resetSessionForTests();
  });

  it("removes it once the server reports the recording stopped", async () => {
    const controller = await keeping("rec-9", ALICE);
    apiMocks.getRecording.mockResolvedValue({ id: "rec-9", status: "PROCESSED" });

    await controller.refreshPausedRecording();
    await settle(controller);

    expect(apiMocks.getRecording).toHaveBeenCalledWith("rec-9");
    expect(await keptSequences(controller, "rec-9")).toEqual([]);
  });

  it("keeps it, without asking, for a capture the server lists as unfinished", async () => {
    // Paused here, or taken over and still running on another device.
    const controller = await keeping("rec-9", ALICE);
    apiMocks.getUnfinishedCaptures.mockResolvedValue([
      { id: "rec-9", status: "UPLOADING", client_status: "RECORDING" },
    ]);

    await controller.refreshPausedRecording();
    await settle(controller);

    expect(apiMocks.getRecording).not.toHaveBeenCalled();
    expect(await keptSequences(controller, "rec-9")).toEqual([4, 5]);
  });

  it("keeps it while the server still holds the recording as a capture", async () => {
    // Started after the list was read, so only the lookup sees it.
    const controller = await keeping("rec-9", ALICE);
    apiMocks.getRecording.mockResolvedValue({ id: "rec-9", status: "UPLOADING" });

    await controller.refreshPausedRecording();
    await settle(controller);

    expect(await keptSequences(controller, "rec-9")).toEqual([4, 5]);
  });

  it("removes it for a recording that is gone, signed in as the account that kept it", async () => {
    // Discarded on another device.
    const controller = await keeping("rec-9", ALICE);
    apiMocks.getRecording.mockRejectedValue(notFound());

    await controller.refreshPausedRecording();
    await settle(controller);

    expect(await keptSequences(controller, "rec-9")).toEqual([]);
  });

  it("keeps it for a 404 while signed in as another account, and asks only once a page", async () => {
    // The server hides another account's recordings behind the same 404.
    const controller = await keeping("rec-9", ALICE);
    apiMocks.getUserMe.mockResolvedValue({ id: 8, username: "bob" });
    apiMocks.getRecording.mockRejectedValue(notFound());

    await controller.refreshPausedRecording();
    await settle(controller);
    await controller.refreshPausedRecording();
    await settle(controller);

    expect(await keptSequences(controller, "rec-9")).toEqual([4, 5]);
    expect(apiMocks.getRecording).toHaveBeenCalledTimes(1);
  });

  it("keeps it for a 404 when it was kept without its owner", async () => {
    const controller = await keeping("rec-9");
    apiMocks.getRecording.mockRejectedValue(notFound());

    await controller.refreshPausedRecording();
    await settle(controller);

    expect(await keptSequences(controller, "rec-9")).toEqual([4, 5]);
  });

  it("asks the server who is signed in rather than trusting a page still loading", async () => {
    // The page has not heard from `/users/me` yet.
    resetSessionForTests();
    const controller = await keeping("rec-9", ALICE);
    apiMocks.getRecording.mockRejectedValue(notFound());

    await controller.refreshPausedRecording();
    await settle(controller);

    expect(apiMocks.getUserMe).toHaveBeenCalledTimes(1);
    expect(await keptSequences(controller, "rec-9")).toEqual([]);
  });

  it("keeps it when the server cannot be asked, and asks again next time", async () => {
    const controller = await keeping("rec-9", ALICE);
    apiMocks.getRecording.mockRejectedValueOnce(new AxiosError("Network Error", "ERR_NETWORK"));
    apiMocks.getRecording.mockResolvedValueOnce({ id: "rec-9", status: "QUEUED" });

    await controller.refreshPausedRecording();
    await settle(controller);
    expect(await keptSequences(controller, "rec-9")).toEqual([4, 5]);

    await controller.refreshPausedRecording();
    await settle(controller);
    expect(await keptSequences(controller, "rec-9")).toEqual([]);
  });

  it("leaves other recordings' audio alone", async () => {
    const controller = await keeping("rec-9", ALICE);
    await storeOf(controller).put(kept("rec-2", 0, ALICE));
    apiMocks.getRecording.mockImplementation(async (recordingId: string) => ({
      id: recordingId,
      status: recordingId === "rec-9" ? "QUEUED" : "PAUSED",
    }));

    await controller.refreshPausedRecording();
    await settle(controller);

    expect(await keptSequences(controller, "rec-9")).toEqual([]);
    expect(await keptSequences(controller, "rec-2")).toEqual([0]);
  });

  it("keeps the signed-in account with the audio a capture keeps", async () => {
    pickSourceMocks.pickCaptureSource.mockResolvedValue({
      mode: "microphone_only",
      displayStream: null,
      microphoneStream: {} as MediaStream,
      captureReport: { mode: "microphone_only", notes: [] },
      release: vi.fn(),
    });
    apiMocks.initRecording.mockResolvedValue({ id: "rec-1", name: "Weekly sync", capture_epoch: 0 });
    apiMocks.reportRecordingCaptureSources.mockResolvedValue(undefined);
    featureDetectMocks.detectCaptureSupport.mockReturnValue({
      supported: true,
      mode: "microphone_only",
    });
    vi.mocked(createSegmentUploader).mockClear();
    const controller = new CaptureController();
    controllers.push(controller);

    await controller.start("Weekly sync");

    expect(vi.mocked(createSegmentUploader).mock.calls[0][0].ownerId).toBe(ALICE);
  });

  describe("keeping it to download rather than removing it (issue #605)", () => {
    const BOB = 8;

    const flacKept = (recordingId: string, sequence: number) => {
      const pcm = new Int16Array(32_000 * 2).fill(sequence);
      return {
        ...kept(recordingId, sequence, ALICE),
        blob: new Blob([encodeFlac(pcm, 2, 16_000).buffer as ArrayBuffer], { type: "audio/flac" }),
      };
    };

    const leftOutOf = (controller: CaptureController) =>
      (controller as unknown as { leftOutAudio: LeftOutAudioStore }).leftOutAudio;

    const download = (id: string, ownerId: number, keptAt = Date.now()): LeftOutAudio => ({
      id,
      recordingId: "rec-3",
      ownerId,
      blob: new Blob(["fLaC"], { type: "audio/flac" }),
      seconds: 60,
      keptAt,
    });

    const offeredIds = () =>
      notificationMocks.addNotification.mock.calls
        .map(([notification]) => String(notification.id))
        .filter((id) => id.startsWith("left-out-audio-"));

    beforeEach(() => {
      notificationMocks.addNotification.mockReset();
    });

    it("keeps it, under the recording's name, once the server reports the recording stopped", async () => {
      const controller = new CaptureController();
      controllers.push(controller);
      await storeOf(controller).put(flacKept("rec-9", 4));
      await storeOf(controller).put(flacKept("rec-9", 5));
      apiMocks.getRecording.mockResolvedValue({ id: "rec-9", name: "Board call", status: "PROCESSED" });

      await controller.refreshPausedRecording();
      await settle(controller);

      const [audio] = await leftOutOf(controller).list();
      expect(audio).toMatchObject({
        recordingId: "rec-9",
        recordingName: "Board call",
        ownerId: ALICE,
        seconds: 4,
      });
      expect(await keptSequences(controller, "rec-9")).toEqual([]);
      await vi.waitFor(() => expect(offeredIds()).toContain(`left-out-audio-${audio.id}`));
    });

    it("keeps nothing of a recording gone for the account that kept it", async () => {
      const controller = new CaptureController();
      controllers.push(controller);
      await storeOf(controller).put(flacKept("rec-9", 4));
      apiMocks.getRecording.mockRejectedValue(notFound());

      await controller.refreshPausedRecording();
      await settle(controller);

      expect(await keptSequences(controller, "rec-9")).toEqual([]);
      expect(await leftOutOf(controller).list()).toEqual([]);
    });

    it("offers what it kept only to the account that recorded it", async () => {
      const controller = new CaptureController();
      controllers.push(controller);
      await leftOutOf(controller).put(download("alices", ALICE));
      await leftOutOf(controller).put(download("bobs", BOB));

      await controller.refreshPausedRecording();
      await settle(controller);

      await vi.waitFor(() => expect(offeredIds()).toEqual(["left-out-audio-alices"]));
      expect((await leftOutOf(controller).list()).map((audio) => audio.id)).toEqual([
        "alices",
        "bobs",
      ]);
    });

    it("removes what nobody took within a week", async () => {
      const controller = new CaptureController();
      controllers.push(controller);
      await leftOutOf(controller).put(download("old", BOB, Date.now() - LEFT_OUT_AUDIO_KEEP_MS - 1));

      await controller.refreshPausedRecording();
      await settle(controller);

      await vi.waitFor(async () => expect(await leftOutOf(controller).list()).toEqual([]));
      expect(offeredIds()).toEqual([]);
    });
  });

  describe("screen study", () => {
    const videoTrack = {
      readyState: "live",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      stop: vi.fn(),
    };
    const audioTrack = { ...videoTrack, stop: vi.fn() };

    const sharedSources = () => ({
      mode: "shared_audio",
      displayStream: {
        getAudioTracks: () => [audioTrack],
        getVideoTracks: () => [videoTrack],
      },
      microphoneStream: {} as MediaStream,
      captureReport: {
        mode: "shared_audio",
        requested_microphone_device_id: null,
        requested_microphone_label: null,
        available_microphones: [],
        browser_microphone_track: null,
        browser_display_audio_track: null,
        browser_display_video_track: null,
        shared_audio_available: true,
        notes: [],
      },
      release: vi.fn(),
    });

    const lastRecorderOptions = () =>
      vi.mocked(createBrowserRecorder).mock.calls.at(-1)?.[0] as Parameters<
        typeof createBrowserRecorder
      >[0];

    beforeEach(() => {
      Object.values(screenStudyMocks.session).forEach((value) => {
        if (typeof value === "function" && "mockClear" in value) {
          value.mockClear();
        }
      });
      screenStudyMocks.startScreenStudySession.mockClear();
      videoTrack.stop.mockClear();
      apiMocks.initRecording.mockResolvedValue({ id: "rec-study", name: "Study" });
    });

    it("samples nothing when the box was not ticked", async () => {
      pickSourceMocks.pickCaptureSource.mockResolvedValue(sharedSources());
      const controller = new CaptureController();

      await controller.start("");

      expect(screenStudyMocks.startScreenStudySession).not.toHaveBeenCalled();
      expect(lastRecorderOptions().onAudioTick).toBeUndefined();
    });

    it("starts on the shared video track, ticked by the recorder at the study's rate", async () => {
      pickSourceMocks.pickCaptureSource.mockResolvedValue(sharedSources());
      const controller = new CaptureController();

      await controller.start("", { screenStudy: { rateHz: 2 } });

      await vi.waitFor(() =>
        expect(screenStudyMocks.startScreenStudySession).toHaveBeenCalledWith(
          expect.objectContaining({ recordingId: "rec-study", displayTrack: videoTrack }),
        ),
      );
      const options = lastRecorderOptions();
      expect(options.audioTickSeconds).toBe(0.5);
      options.onAudioTick?.({ sequence: 3, offsetMs: 500 });
      expect(screenStudyMocks.session.tick).toHaveBeenCalledWith({ sequence: 3, offsetMs: 500 });
      // The share itself is never stopped by the study.
      expect(videoTrack.stop).not.toHaveBeenCalled();
    });

    it("pauses with the recording and resumes with it", async () => {
      pickSourceMocks.pickCaptureSource.mockResolvedValue(sharedSources());
      apiMocks.pauseRecordingCapture.mockResolvedValue({
        recording_id: "rec-study",
        status: "PAUSED",
        last_sequence: 0,
      });
      apiMocks.resumeRecordingCapture.mockResolvedValue({
        recording_id: "rec-study",
        status: "UPLOADING",
        last_sequence: 0,
        capture_epoch: 0,
      });
      const controller = new CaptureController();
      await controller.start("", { screenStudy: { rateHz: 1 } });
      await vi.waitFor(() => expect(screenStudyMocks.startScreenStudySession).toHaveBeenCalled());

      await controller.pause();
      expect(screenStudyMocks.session.pause).toHaveBeenCalled();

      await controller.resume("rec-study");
      expect(screenStudyMocks.session.resume).toHaveBeenCalled();
    });

    it("Stop finishes the study before the share is released", async () => {
      const sources = sharedSources();
      pickSourceMocks.pickCaptureSource.mockResolvedValue(sources);
      apiMocks.finalizeRecordingCapture.mockResolvedValue({ id: "rec-study", status: "QUEUED" });
      const controller = new CaptureController();
      await controller.start("", { screenStudy: { rateHz: 1 } });
      await vi.waitFor(() => expect(screenStudyMocks.startScreenStudySession).toHaveBeenCalled());

      await controller.stop();

      expect(screenStudyMocks.session.finish).toHaveBeenCalledTimes(1);
      expect(screenStudyMocks.session.finish.mock.invocationCallOrder[0]).toBeLessThan(
        sources.release.mock.invocationCallOrder[0],
      );
      expect(screenStudyMocks.session.abort).not.toHaveBeenCalled();
    });

    it("discarding drops the study at once", async () => {
      pickSourceMocks.pickCaptureSource.mockResolvedValue(sharedSources());
      const controller = new CaptureController();
      await controller.start("", { screenStudy: { rateHz: 1 } });
      await vi.waitFor(() => expect(screenStudyMocks.startScreenStudySession).toHaveBeenCalled());

      await controller.cancel();

      expect(screenStudyMocks.session.abort).toHaveBeenCalledTimes(1);
      expect(screenStudyMocks.session.finish).not.toHaveBeenCalled();
    });

    it("records on when the study cannot start", async () => {
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      screenStudyMocks.startScreenStudySession.mockImplementationOnce(() => {
        throw new Error("no MediaStreamTrackProcessor");
      });
      pickSourceMocks.pickCaptureSource.mockResolvedValue(sharedSources());
      const controller = new CaptureController();

      await controller.start("", { screenStudy: { rateHz: 1 } });
      await vi.waitFor(() => expect(warn).toHaveBeenCalled());

      expect(controller.getState().status).toBe("recording");
      expect(controller.getState().error).toBeNull();
      warn.mockRestore();
    });
  });
});
