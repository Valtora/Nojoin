import { AxiosError } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  noteSessionRefusal,
  noteSignedIn,
  resetSessionForTests,
  useSessionStore,
} from "@/lib/session";

import { CaptureController } from "./controller";
import { PickSourceError } from "./pickSource";
import { createBrowserRecorder } from "./recorder";
import { createSegmentUploader } from "./uploader";

const apiMocks = vi.hoisted(() => ({
  discardRecordingCapture: vi.fn(),
  getRecording: vi.fn(),
  finalizeRecordingCapture: vi.fn(),
  pauseRecordingCapture: vi.fn(),
  getUnfinishedCaptures: vi.fn(),
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
const connectivityMocks = vi.hoisted(() => ({ status: "online" as string }));

vi.mock("@/lib/api", () => ({
  discardRecordingCapture: apiMocks.discardRecordingCapture,
  finalizeRecordingCapture: apiMocks.finalizeRecordingCapture,
  getUnfinishedCaptures: apiMocks.getUnfinishedCaptures,
  getRecording: apiMocks.getRecording,
  initRecording: apiMocks.initRecording,
  isActiveRecordingConflictDetail: vi.fn(() => false),
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
  useConnectivityStore: { getState: () => ({ status: connectivityMocks.status }) },
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
    noiseSuppression: true,
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
    stop: vi.fn(),
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
      recorder: {
        pause: async () => {
          calls.push("recorder");
        },
      },
      uploader: {
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
        noiseSuppression: true,
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
      buildConflictError("Recording is no longer accepting capture uploads"),
    );

    const controller = new CaptureController() as any;
    controller.state = {
      ...controller.getState(),
      status: "recording",
      recordingId: "rec-1",
    };
    controller.runtime = null;

    await expect(controller.stop()).rejects.toThrow(
      "Recording is no longer accepting capture uploads",
    );

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
    expect(apiMocks.discardRecordingCapture).toHaveBeenCalledWith("rec-1");
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
    });
    apiMocks.resumeRecordingCapture.mockReset();
    apiMocks.resumeRecordingCapture.mockResolvedValue({
      recording_id: "rec-1",
      status: "UPLOADING",
      last_sequence: 4,
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
    expect(controller.getState().status).toBe("recording");

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

    expect(apiMocks.discardRecordingCapture).toHaveBeenCalledWith("rec-1");
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
