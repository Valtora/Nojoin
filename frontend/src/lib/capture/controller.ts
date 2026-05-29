import { AxiosError } from "axios";

import {
  discardRecordingCapture,
  finalizeRecordingCapture,
  getPausedRecordings,
  initRecording,
  isActiveRecordingConflictDetail,
  pauseRecordingCapture,
  resumeRecordingCapture,
} from "@/lib/api";
import type { Recording, RecordingId } from "@/types";

import { detectCaptureSupport } from "./featureDetect";
import { CaptureLifecycle, sendPauseBeacon } from "./lifecycle";
import { createCaptureMixer, type CaptureMixer } from "./mixer";
import { pickCaptureSource, PickSourceError, type PickedCaptureSources } from "./pickSource";
import { createBrowserRecorder, type BrowserRecorder } from "./recorder";
import {
  clearPausedCaptureContext,
  DEFAULT_CAPTURE_LEVELS,
  type CaptureSettings,
  type CaptureState,
  type StartCaptureResult,
  readCaptureSettings,
  readPausedCaptureContext,
  writeCaptureSettings,
  writePausedCaptureContext,
} from "./shared";
import { createSegmentUploader, type SegmentUploader } from "./uploader";
import { createWaveformMonitor, type WaveformMonitor } from "./waveform";

type StateListener = (state: CaptureState) => void;

interface ActiveRuntime {
  recordingId: RecordingId;
  sources: PickedCaptureSources;
  mixer: CaptureMixer;
  recorder: BrowserRecorder;
  uploader: SegmentUploader;
  waveform: WaveformMonitor;
}

const sequenceToElapsedSeconds = (lastSequence: number) =>
  lastSequence >= 0 ? (lastSequence + 1) * 2 : 0;

const FINALIZE_UPLOAD_IN_PROGRESS_DETAIL =
  "Recording upload is still in progress; finalize after all segment uploads complete.";

const FINALIZE_RETRY_DELAYS_MS = [
  250,
  500,
  1_000,
  1_500,
  2_500,
  4_000,
  6_000,
  8_000,
  10_000,
  12_000,
  15_000,
];

const wait = (ms: number) =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

const formatCaptureError = (error: unknown) => {
  if (error instanceof PickSourceError) {
    return error.message;
  }

  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (detail && typeof detail.message === "string") {
      return detail.message;
    }
    if (typeof error.message === "string") {
      return error.message;
    }
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "The browser capture flow failed unexpectedly.";
};

const formatUnsupportedMessage = (reason: CaptureState["support"]["reason"]) => {
  switch (reason) {
    case "firefox":
      return "Recording capture is not supported in Firefox. Use a Chromium browser on Windows or Linux.";
    case "safari":
      return "Recording capture is not supported in Safari. Use a Chromium browser on Windows or Linux.";
    case "macos_chromium":
      return "Recording capture is not supported on macOS Chromium. Use Windows or Linux for native browser capture.";
    case "mobile":
      return "Recording capture is not supported on mobile browsers.";
    default:
      return "This browser does not support the capture APIs required for recording.";
  }
};

const resolveCaptureMode = (support: CaptureState["support"]) =>
  support.mode ?? "shared_audio";

export class CaptureController {
  private state: CaptureState;

  private readonly listeners = new Set<StateListener>();

  private runtime: ActiveRuntime | null = null;

  private readonly lifecycle: CaptureLifecycle;

  private elapsedTimerId: ReturnType<typeof setInterval> | null = null;

  private elapsedTimerBaseSeconds = 0;

  private elapsedTimerStartedAt = 0;

  constructor() {
    const pausedContext = readPausedCaptureContext();
    this.state = {
      status: pausedContext ? "paused" : "idle",
      support: detectCaptureSupport(),
      levels: DEFAULT_CAPTURE_LEVELS,
      error: null,
      lastSequence: pausedContext?.lastSequence ?? -1,
      elapsedSeconds: sequenceToElapsedSeconds(pausedContext?.lastSequence ?? -1),
      recordingId: pausedContext?.recordingId ?? null,
      pausedRecording: null,
      runtimeActive: false,
      settings: readCaptureSettings(),
    };

    this.lifecycle = new CaptureLifecycle({
      getRecordingId: () => this.state.recordingId,
      shouldGuardExit: () => Boolean(this.runtime),
      onGuardedExit: (request) => this.handleGuardedExit(request),
    });
  }

  getState = () => this.state;

  subscribe = (listener: StateListener) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  attachLifecycle = (routeSignature: string) => {
    this.lifecycle.attach(routeSignature);
  };

  updateRouteSignature = (routeSignature: string) => {
    const routePath = routeSignature.split("?", 1)[0];
    if (this.state.recordingId && routePath === `/recordings/${this.state.recordingId}`) {
      this.lifecycle.updateRouteSignature(routeSignature, { guard: false });
      return;
    }

    this.lifecycle.updateRouteSignature(routeSignature);
  };

  destroy = async () => {
    this.lifecycle.detach();
    await this.disposeRuntime();
  };

  refreshPausedRecording = async () => {
    const pausedRecordings = await getPausedRecordings();
    this.setState({
      pausedRecording: pausedRecordings[0] ?? null,
    });
    return pausedRecordings[0] ?? null;
  };

  updateSettings = (patch: Partial<CaptureSettings>) => {
    const nextSettings = {
      ...this.state.settings,
      ...patch,
    };

    writeCaptureSettings(nextSettings);
    this.setState({ settings: nextSettings });
  };

  clearError = () => {
    if (!this.state.error) {
      return;
    }

    this.setState({ error: null });
  };

  start = async (name?: string): Promise<StartCaptureResult> => {
    const support = detectCaptureSupport();
    this.setState({ support });
    if (!support.supported) {
      const message = formatUnsupportedMessage(support.reason);
      this.setState({ error: message, status: "error" });
      throw new Error(message);
    }

    this.setState({ status: "starting", error: null });

    let initResponse;
    try {
      initResponse = await initRecording(name);
    } catch (error) {
      const detail =
        error instanceof AxiosError ? error.response?.data?.detail : null;

      if (isActiveRecordingConflictDetail(detail)) {
        await this.refreshPausedRecording().catch(() => {});
        this.setState({
          status: "paused",
          recordingId: detail.recording_id,
          elapsedSeconds: sequenceToElapsedSeconds(this.state.lastSequence),
          error: detail.message,
        });
      } else {
        this.setState({ status: "error", error: formatCaptureError(error) });
      }
      throw error;
    }

    this.setState({
      recordingId: initResponse.id,
      lastSequence: -1,
      elapsedSeconds: 0,
    });

    let sources: PickedCaptureSources | null = null;
    try {
      sources = await pickCaptureSource({
        mode: resolveCaptureMode(support),
        microphoneDeviceId: this.state.settings.microphoneDeviceId,
      });
      await this.activateRuntime({
        recordingId: initResponse.id,
        startSequence: 0,
        sources,
        elapsedSeconds: 0,
      });
      sources = null;
      this.setState({
        status: "recording",
        recordingId: initResponse.id,
        error: null,
      });
      clearPausedCaptureContext();
      await this.refreshPausedRecording().catch(() => {});
      return {
        recordingId: initResponse.id,
        name: initResponse.name,
        resumed: false,
      };
    } catch (error) {
      sources?.release();
      await discardRecordingCapture(initResponse.id).catch(() => {});
      this.setState({
        status: "error",
        error: formatCaptureError(error),
        recordingId: null,
        lastSequence: -1,
        elapsedSeconds: 0,
      });
      throw error;
    }
  };

  pause = async () => {
    if (!this.runtime || !this.state.recordingId) {
      return;
    }

    if (this.state.status === "paused") {
      return;
    }

    await this.runtime.recorder.pause();
    await this.runtime.uploader.waitForIdle();
    this.stopElapsedTimer();
    const response = await pauseRecordingCapture(this.state.recordingId);
    writePausedCaptureContext({
      recordingId: response.recording_id,
      lastSequence: Math.max(response.last_sequence, this.state.lastSequence),
      persistedAt: Date.now(),
    });
    this.setState({
      status: "paused",
      lastSequence: Math.max(response.last_sequence, this.state.lastSequence),
      elapsedSeconds: Math.max(
        this.state.elapsedSeconds,
        sequenceToElapsedSeconds(response.last_sequence),
      ),
    });
    await this.refreshPausedRecording().catch(() => {});
  };

  resume = async (recordingId?: RecordingId): Promise<StartCaptureResult> => {
    const targetRecordingId =
      recordingId ?? this.state.pausedRecording?.id ?? this.state.recordingId;
    if (!targetRecordingId) {
      throw new Error("No paused recording is available to resume.");
    }

    if (this.runtime && this.state.status === "paused") {
      const response = await resumeRecordingCapture(targetRecordingId);
      this.runtime.recorder.resume();
      this.startElapsedTimer(this.state.elapsedSeconds);
      clearPausedCaptureContext();
      this.setState({
        status: "recording",
        recordingId: targetRecordingId,
        lastSequence: Math.max(response.last_sequence, this.state.lastSequence),
        error: null,
      });
      await this.refreshPausedRecording().catch(() => {});
      return { recordingId: targetRecordingId, resumed: true };
    }

    const support = detectCaptureSupport();
    this.setState({ support });
    if (!support.supported) {
      const message = formatUnsupportedMessage(support.reason);
      this.setState({ error: message, status: "error" });
      throw new Error(message);
    }

    this.setState({
      status: "starting",
      recordingId: targetRecordingId,
      error: null,
    });

    let sources: PickedCaptureSources | null = null;
    let resumeResponse: Awaited<ReturnType<typeof resumeRecordingCapture>> | null = null;

    try {
      sources = await pickCaptureSource({
        mode: resolveCaptureMode(support),
        microphoneDeviceId: this.state.settings.microphoneDeviceId,
      });
      resumeResponse = await resumeRecordingCapture(targetRecordingId);
      await this.activateRuntime({
        recordingId: targetRecordingId,
        startSequence: resumeResponse.last_sequence + 1,
        sources,
        elapsedSeconds: sequenceToElapsedSeconds(resumeResponse.last_sequence),
      });
      sources = null;
      clearPausedCaptureContext();
      this.setState({
        status: "recording",
        recordingId: targetRecordingId,
        lastSequence: resumeResponse.last_sequence,
        elapsedSeconds: sequenceToElapsedSeconds(resumeResponse.last_sequence),
        error: null,
      });
      await this.refreshPausedRecording().catch(() => {});
      return { recordingId: targetRecordingId, resumed: true };
    } catch (error) {
      sources?.release();
      if (resumeResponse) {
        await pauseRecordingCapture(targetRecordingId).catch(() => {});
      }
      this.setState({
        status: "error",
        error: formatCaptureError(error),
      });
      throw error;
    }
  };

  stop = async (): Promise<Recording> => {
    if (!this.state.recordingId) {
      throw new Error("No active recording is available to finalize.");
    }

    this.setState({ status: "finalizing", error: null });
    if (this.state.status === "paused") {
      await resumeRecordingCapture(this.state.recordingId);
    }

    try {
      if (this.runtime) {
        await this.runtime.recorder.stop({ emitTail: true });
        await this.runtime.uploader.waitForIdle();
      }
      const recording = await this.finalizeRecordingWhenReady(this.state.recordingId);
      clearPausedCaptureContext();
      await this.disposeRuntime();
      this.setState({
        status: "idle",
        error: null,
        lastSequence: -1,
        elapsedSeconds: 0,
        recordingId: null,
        pausedRecording: null,
        runtimeActive: false,
        levels: DEFAULT_CAPTURE_LEVELS,
      });
      await this.refreshPausedRecording().catch(() => {});
      this.lifecycle.updateRecordingId(null);
      return recording;
    } catch (error) {
      const message = formatCaptureError(error);
      this.setState({ status: "error", error: message });
      throw new Error(message);
    }
  };

  cancel = async (recordingId?: RecordingId) => {
    const targetRecordingId =
      recordingId ?? this.state.pausedRecording?.id ?? this.state.recordingId;
    if (!targetRecordingId) {
      return;
    }

    if (this.runtime) {
      await this.runtime.recorder.stop({ emitTail: false }).catch(() => {});
      await this.disposeRuntime();
    }

    await discardRecordingCapture(targetRecordingId);
    clearPausedCaptureContext();
    this.setState({
      status: "idle",
      error: null,
      lastSequence: -1,
      elapsedSeconds: 0,
      recordingId: null,
      pausedRecording: null,
      runtimeActive: false,
      levels: DEFAULT_CAPTURE_LEVELS,
    });
    this.lifecycle.updateRecordingId(null);
    await this.refreshPausedRecording().catch(() => {});
  };

  private activateRuntime = async (options: {
    recordingId: RecordingId;
    startSequence: number;
    sources: PickedCaptureSources;
    elapsedSeconds?: number;
  }) => {
    await this.disposeRuntime();

    const mixer = await createCaptureMixer({
      displayStream: options.sources.displayStream,
      microphoneStream: options.sources.microphoneStream,
    });

    const uploader = createSegmentUploader({
      recordingId: options.recordingId,
      initialSequence: options.startSequence,
      onUploaded: (sequence) => {
        this.setState({
          lastSequence: sequence,
          elapsedSeconds: Math.max(
            this.state.elapsedSeconds,
            sequenceToElapsedSeconds(sequence),
          ),
        });
      },
      onFatal: async (error) => {
        await this.handleUploaderFatal(options.recordingId, error);
      },
    });

    const waveform = createWaveformMonitor({
      systemAnalyser: mixer.systemAnalyser,
      microphoneAnalyser: mixer.microphoneAnalyser,
      mixedAnalyser: mixer.mixedAnalyser,
      onBeforeLevels: mixer.updateAutomaticGain,
      onLevels: (levels) => {
        this.setState({ levels });
      },
    });

    const recorder = createBrowserRecorder({
      stream: mixer.outputStream,
      startSequence: options.startSequence,
      onChunk: ({ sequence, blob }) => {
        uploader.enqueue(sequence, blob);
      },
      onError: (error) => {
        this.setState({ status: "error", error: error.message });
      },
    });

    recorder.start();
    waveform.start();
    this.runtime = {
      recordingId: options.recordingId,
      sources: options.sources,
      mixer,
      recorder,
      uploader,
      waveform,
    };
    this.setState({ runtimeActive: true });
    this.startElapsedTimer(options.elapsedSeconds ?? this.state.elapsedSeconds);
    this.lifecycle.updateRecordingId(options.recordingId);
    this.lifecycle.resetGuard();
  };

  private handleUploaderFatal = async (
    recordingId: RecordingId,
    error: Error,
  ) => {
    this.setState({ error: error.message || formatCaptureError(error) });

    if (!this.runtime || this.state.recordingId !== recordingId) {
      return;
    }

    try {
      await this.runtime.recorder.pause();
      await this.runtime.uploader.waitForIdle();
      const response = await pauseRecordingCapture(recordingId);
      writePausedCaptureContext({
        recordingId,
        lastSequence: Math.max(response.last_sequence, this.state.lastSequence),
        persistedAt: Date.now(),
      });
      this.setState({
        status: "paused",
        lastSequence: Math.max(response.last_sequence, this.state.lastSequence),
        elapsedSeconds: Math.max(
          this.state.elapsedSeconds,
          sequenceToElapsedSeconds(response.last_sequence),
        ),
      });
      this.stopElapsedTimer();
      await this.refreshPausedRecording().catch(() => {});
    } catch (pauseError) {
      this.setState({
        status: "error",
        error: formatCaptureError(pauseError),
      });
    }
  };

  private finalizeRecordingWhenReady = async (recordingId: RecordingId) => {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await finalizeRecordingCapture(recordingId);
      } catch (error) {
        const detail = error instanceof AxiosError ? error.response?.data?.detail : null;
        const canRetry =
          error instanceof AxiosError &&
          error.response?.status === 409 &&
          detail === FINALIZE_UPLOAD_IN_PROGRESS_DETAIL &&
          attempt < FINALIZE_RETRY_DELAYS_MS.length;

        if (!canRetry) {
          throw error;
        }

        await wait(FINALIZE_RETRY_DELAYS_MS[attempt]);
      }
    }
  };

  private handleGuardedExit = async (request: {
    reason: "pagehide" | "beforeunload" | "route-change";
    useBeacon: boolean;
  }) => {
    if (!this.runtime || !this.state.recordingId) {
      return;
    }

    const recordingId = this.state.recordingId;
    const lastSequence = this.state.lastSequence;
    const wasAlreadyPaused = this.state.status === "paused";

    try {
      await this.runtime.recorder.stop({ emitTail: false });

      if (!wasAlreadyPaused) {
        const paused = request.useBeacon ? sendPauseBeacon(recordingId) : false;
        if (!paused) {
          const response = await pauseRecordingCapture(recordingId);
          this.setState({
            lastSequence: Math.max(response.last_sequence, lastSequence),
            elapsedSeconds: Math.max(
              this.state.elapsedSeconds,
              sequenceToElapsedSeconds(response.last_sequence),
            ),
          });
        }
      }
    } finally {
      writePausedCaptureContext({
        recordingId,
        lastSequence,
        persistedAt: Date.now(),
      });
      await this.disposeRuntime();
      this.setState({
        status: "paused",
        recordingId,
        elapsedSeconds: this.state.elapsedSeconds,
        levels: DEFAULT_CAPTURE_LEVELS,
      });
      await this.refreshPausedRecording().catch(() => {});
    }
  };

  private disposeRuntime = async () => {
    if (!this.runtime) {
      return;
    }

    const runtime = this.runtime;
    this.runtime = null;
    this.stopElapsedTimer();
    runtime.waveform.stop();
    runtime.uploader.dispose();
    runtime.sources.release();
    await runtime.mixer.dispose();
    this.setState({ levels: DEFAULT_CAPTURE_LEVELS, runtimeActive: false });
  };

  private startElapsedTimer(initialElapsedSeconds: number) {
    this.stopElapsedTimer();
    this.elapsedTimerBaseSeconds = initialElapsedSeconds;
    this.elapsedTimerStartedAt = Date.now();
    this.setState({ elapsedSeconds: initialElapsedSeconds });
    this.elapsedTimerId = setInterval(() => {
      const elapsedSeconds =
        this.elapsedTimerBaseSeconds +
        Math.floor((Date.now() - this.elapsedTimerStartedAt) / 1_000);
      this.setState({ elapsedSeconds });
    }, 1_000);
  }

  private stopElapsedTimer() {
    if (this.elapsedTimerId) {
      clearInterval(this.elapsedTimerId);
      this.elapsedTimerId = null;
    }
  }

  private setState(patch: Partial<CaptureState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener(this.state));
  }
}

export const createCaptureController = () => new CaptureController();
