import { AxiosError } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureController } from "./controller";
import { PickSourceError } from "./pickSource";

const apiMocks = vi.hoisted(() => ({
  discardRecordingCapture: vi.fn(),
  getRecording: vi.fn(),
  finalizeRecordingCapture: vi.fn(),
  pauseRecordingCapture: vi.fn(),
  getPausedRecordings: vi.fn(),
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
  getPausedRecordings: apiMocks.getPausedRecordings,
  getRecording: apiMocks.getRecording,
  initRecording: apiMocks.initRecording,
  isActiveRecordingConflictDetail: vi.fn(() => false),
  pauseRecordingCapture: apiMocks.pauseRecordingCapture,
  reportRecordingCaptureSources: apiMocks.reportRecordingCaptureSources,
  resumeRecordingCapture: apiMocks.resumeRecordingCapture,
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
  readPausedCaptureContext: vi.fn(() => null),
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
    systemAnalyser: {},
    microphoneAnalyser: {},
    mixedAnalyser: {},
    updateAutomaticGain: vi.fn(),
    dispose: vi.fn(),
  })),
}));

vi.mock("./recorder", () => ({
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
    waitForIdle: vi.fn(),
    recover: vi.fn(() => true),
    dispose: vi.fn(),
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
    apiMocks.getPausedRecordings.mockReset();
    apiMocks.initRecording.mockReset();
    apiMocks.reportRecordingCaptureSources.mockReset();
    apiMocks.reportRecordingCaptureSources.mockResolvedValue(undefined);
    apiMocks.resumeRecordingCapture.mockReset();
    apiMocks.resumeRecordingCapture.mockResolvedValue({
      recording_id: "rec-1",
      status: "UPLOADING",
      last_sequence: 0,
    });
    apiMocks.getPausedRecordings.mockResolvedValue([]);
    featureDetectMocks.detectCaptureSupport.mockReset();
    featureDetectMocks.detectCaptureSupport.mockReturnValue({
      supported: true,
      mode: "shared_audio",
    });
    pickSourceMocks.pickCaptureSource.mockReset();
    notificationMocks.addNotification.mockReset();
    apiMocks.getRecording.mockReset();
    apiMocks.getRecording.mockResolvedValue({ id: "rec-1" });
    connectivityMocks.status = "online";
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

    expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalledWith("rec-1");
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
    expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalledWith("rec-9");
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

    expect(stages).toEqual([
      "stopping-recorder",
      "flushing-uploads",
      "releasing-media",
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
    expect(apiMocks.finalizeRecordingCapture).toHaveBeenCalledWith("rec-1");
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
