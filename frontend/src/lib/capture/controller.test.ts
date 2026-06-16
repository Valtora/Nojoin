import { AxiosError } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureController } from "./controller";
import { PickSourceError } from "./pickSource";

const apiMocks = vi.hoisted(() => ({
  discardRecordingCapture: vi.fn(),
  finalizeRecordingCapture: vi.fn(),
  pauseRecordingCapture: vi.fn(),
  getPausedRecordings: vi.fn(),
  initRecording: vi.fn(),
  reportRecordingCaptureSources: vi.fn(),
}));

const featureDetectMocks = vi.hoisted(() => ({
  detectCaptureSupport: vi.fn(() => ({ supported: true, mode: "shared_audio" })),
}));

const pickSourceMocks = vi.hoisted(() => ({
  pickCaptureSource: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  discardRecordingCapture: apiMocks.discardRecordingCapture,
  finalizeRecordingCapture: apiMocks.finalizeRecordingCapture,
  getPausedRecordings: apiMocks.getPausedRecordings,
  initRecording: apiMocks.initRecording,
  isActiveRecordingConflictDetail: vi.fn(() => false),
  pauseRecordingCapture: apiMocks.pauseRecordingCapture,
  reportRecordingCaptureSources: apiMocks.reportRecordingCaptureSources,
  resumeRecordingCapture: vi.fn(),
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
    apiMocks.getPausedRecordings.mockResolvedValue([]);
    featureDetectMocks.detectCaptureSupport.mockReset();
    featureDetectMocks.detectCaptureSupport.mockReturnValue({
      supported: true,
      mode: "shared_audio",
    });
    pickSourceMocks.pickCaptureSource.mockReset();
    notificationMocks.addNotification.mockReset();
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
});
