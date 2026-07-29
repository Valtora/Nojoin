import { AxiosError } from "axios";

import {
  discardRecordingCapture,
  finalizeRecordingCapture,
  getPausedRecordings,
  initRecording,
  isActiveRecordingConflictDetail,
  pauseRecordingCapture,
  reportRecordingCaptureSources,
  resumeRecordingCapture,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import type { Recording, RecordingId } from "@/types";

import { detectCaptureSupport } from "./featureDetect";
import { CaptureLifecycle, sendPauseBeacon } from "./lifecycle";
import { createCaptureMixer, type CaptureMixer } from "./mixer";
import { pickCaptureSource, PickSourceError, type PickedCaptureSources } from "./pickSource";
import {
  createBrowserRecorder,
  type BrowserRecorder,
  type RecorderStallInfo,
} from "./recorder";
import {
  buildCaptureSourceReportPayload,
  logCaptureSourceReport,
  type CaptureSourceReportPayload,
  type CaptureSourceReportSnapshot,
} from "./sourceReport";
import {
  clearPausedCaptureContext,
  DEFAULT_CAPTURE_LEVELS,
  type CaptureCoverageWarning,
  type CaptureSettings,
  type CaptureState,
  type CaptureStopStage,
  type StartCaptureResponse,
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
  captureReport: CaptureSourceReportSnapshot;
  mixer: CaptureMixer;
  recorder: BrowserRecorder;
  uploader: SegmentUploader;
  waveform: WaveformMonitor;
  displayTracksCleanup?: () => void;
  mediaReleased?: boolean;
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

/** Bound on each pre-finalize stop stage, so none of them can hang finalize. */
const RECORDER_STOP_TIMEOUT_MS = 8_000;

const UPLOAD_FLUSH_TIMEOUT_MS = 60_000;

/**
 * Coverage thresholds for the suspended-tab warning. Both must be exceeded, so
 * the ordinary couple of seconds of trailing segment latency stays quiet.
 */
const COVERAGE_WARNING_MIN_MISSING_SECONDS = 60;

const COVERAGE_WARNING_MIN_RATIO = 0.1;

const COVERAGE_WARNING_INTERVAL_MS = 5 * 60_000;

const withStageTimeout = async <T>(
  stage: CaptureStopStage,
  operation: Promise<T>,
  timeoutMs: number,
): Promise<T | null> => {
  let timeoutId: ReturnType<typeof setTimeout> | null = null;
  const expired = Symbol("expired");
  const timeout = new Promise<typeof expired>((resolve) => {
    timeoutId = setTimeout(() => resolve(expired), timeoutMs);
  });

  try {
    const result = await Promise.race([operation, timeout]);
    if (result === expired) {
      console.warn(`[capture] stop stage "${stage}" timed out after ${timeoutMs}ms`);
      return null;
    }
    return result;
  } finally {
    if (timeoutId) {
      clearTimeout(timeoutId);
    }
  }
};

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

  if (error && typeof error === "object" && "message" in error && typeof error.message === "string") {
    return error.message;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return error ? String(error) : "The browser capture flow failed unexpectedly.";
};

const formatUnsupportedMessage = (reason: CaptureState["support"]["reason"]) => {
  switch (reason) {
    case "firefox":
      return "Recording capture is not supported in Firefox. Use Chrome on desktop or Chrome on Android/iOS for microphone-only capture.";
    case "safari":
      return "Recording capture is not supported in Safari. Use Chrome on desktop or Chrome on Android/iOS for microphone-only capture.";
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

  private lastCoverageNotifiedAt = 0;

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
      finalizeRetry: null,
      stopStage: null,
      coverageWarning: null,
    };

    this.lifecycle = new CaptureLifecycle({
      getRecordingId: () => this.state.recordingId,
      shouldGuardExit: () => Boolean(this.runtime),
      onGuardedExit: (request) => this.handleGuardedExit(request),
      onPageResume: () => this.handlePageResume(),
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
    this.runtime?.mixer.applySettings({
      systemGain: nextSettings.systemGain,
      microphoneGain: nextSettings.microphoneGain,
    });
    this.setState({ settings: nextSettings });
  };

  clearError = () => {
    if (!this.state.error) {
      return;
    }

    this.setState({ error: null });
  };

  private reportCaptureSourceAttempt = async (
    recordingId: RecordingId,
    payload: CaptureSourceReportPayload,
  ) => {
    logCaptureSourceReport(payload);
    try {
      await reportRecordingCaptureSources(recordingId, payload);
    } catch (error) {
      console.warn(
        "[capture] failed to send capture source report",
        recordingId,
        error,
      );
    }
  };

  start = async (name?: string): Promise<StartCaptureResponse> => {
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

    } catch (error: unknown) {
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

    this.lastCoverageNotifiedAt = 0;
    this.setState({
      recordingId: initResponse.id,
      lastSequence: -1,
      elapsedSeconds: 0,
      coverageWarning: null,
      stopStage: null,
    });

    let sources: PickedCaptureSources | null = null;
    try {
      sources = await pickCaptureSource({
        mode: resolveCaptureMode(support),
        microphoneDeviceId: this.state.settings.microphoneDeviceId,
        settings: this.state.settings,
      });

      if (sources.displayStream && sources.displayStream.getAudioTracks().length === 0) {
        useNotificationStore.getState().addNotification({
          type: "warning",
          message: "No system/tab audio detected. Only microphone audio will be recorded.",
        });
      }

      await this.activateRuntime({
        recordingId: initResponse.id,
        startSequence: 0,
        sources,
        captureReport: sources.captureReport,
        elapsedSeconds: 0,
      });
      const captureReport = sources.captureReport;
      sources = null;
      this.setState({
        status: "recording",
        recordingId: initResponse.id,
        error: null,
      });
      await this.reportCaptureSourceAttempt(
        initResponse.id,
        buildCaptureSourceReportPayload(captureReport, {
          attempt_kind: "start",
          outcome: "success",
        }),
      );
      clearPausedCaptureContext();
      await this.refreshPausedRecording().catch(() => {});
      return {
        recordingId: initResponse.id,
        name: initResponse.name,
        resumed: false,
      };

    } catch (error: unknown) {
      if (error instanceof PickSourceError && error.captureReport) {
        await this.reportCaptureSourceAttempt(
          initResponse.id,
          buildCaptureSourceReportPayload(error.captureReport, {
            attempt_kind: "start",
            outcome: "failure",
            failure_code: error.code,
            failure_message: error.message,
          }),
        );
      }
      sources?.release();
      const cancelledByUser =
        error instanceof PickSourceError && error.code === "display_cancelled";
      await discardRecordingCapture(
        initResponse.id,
        cancelledByUser ? "display_picker_cancelled" : undefined,
      ).catch(() => {});

      if (cancelledByUser) {
        this.setState({
          status: "idle",
          error: null,
          recordingId: null,
          lastSequence: -1,
          elapsedSeconds: 0,
        });
        await this.refreshPausedRecording().catch(() => {});
        return null;
      }

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

    // Reusing the existing tracks is only valid while they are still live. After
    // a failed stop the runtime survives with its media released, and resuming
    // onto those dead tracks would record silence.
    if (
      this.runtime &&
      !this.runtime.mediaReleased &&
      this.state.status === "paused"
    ) {
      const response = await resumeRecordingCapture(targetRecordingId);
      // A stalled uploader (for example after a network outage) still holds
      // the queued segments; recovering it retries them before new audio.
      this.runtime.uploader.recover();
      this.runtime.recorder.resume();
      this.startElapsedTimer(this.state.elapsedSeconds);
      clearPausedCaptureContext();
      this.setState({
        status: "recording",
        recordingId: targetRecordingId,
        lastSequence: Math.max(response.last_sequence, this.state.lastSequence),
        error: null,
      });
      await this.reportCaptureSourceAttempt(
        targetRecordingId,
        buildCaptureSourceReportPayload(this.runtime.captureReport, {
          attempt_kind: "resume",
          outcome: "success",
          notes: ["reused_existing_browser_tracks"],
        }),
      );
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
        settings: this.state.settings,
      });

      if (sources.displayStream && sources.displayStream.getAudioTracks().length === 0) {
        useNotificationStore.getState().addNotification({
          type: "warning",
          message: "No system/tab audio detected. Only microphone audio will be recorded.",
        });
      }

      resumeResponse = await resumeRecordingCapture(targetRecordingId);
      await this.activateRuntime({
        recordingId: targetRecordingId,
        startSequence: resumeResponse.last_sequence + 1,
        sources,
        captureReport: sources.captureReport,
        elapsedSeconds: sequenceToElapsedSeconds(resumeResponse.last_sequence),
      });
      const captureReport = sources.captureReport;
      sources = null;
      clearPausedCaptureContext();
      this.setState({
        status: "recording",
        recordingId: targetRecordingId,
        lastSequence: resumeResponse.last_sequence,
        elapsedSeconds: sequenceToElapsedSeconds(resumeResponse.last_sequence),
        error: null,
      });
      await this.reportCaptureSourceAttempt(
        targetRecordingId,
        buildCaptureSourceReportPayload(captureReport, {
          attempt_kind: "resume",
          outcome: "success",
        }),
      );
      await this.refreshPausedRecording().catch(() => {});
      return { recordingId: targetRecordingId, resumed: true };

    } catch (error: unknown) {
      if (error instanceof PickSourceError && error.captureReport) {
        await this.reportCaptureSourceAttempt(
          targetRecordingId,
          buildCaptureSourceReportPayload(error.captureReport, {
            attempt_kind: "resume",
            outcome: "failure",
            failure_code: error.code,
            failure_message: error.message,
          }),
        );
      }
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

  /**
   * Stops capture and queues final processing.
   *
   * Valid whether or not a browser runtime is still attached: a recording whose
   * runtime was torn down (tab reload, guarded exit) is finalized from its
   * uploaded segments alone. The server accepts finalize for a PAUSED recording,
   * so no resume round trip is needed first, which also removes the ordering
   * race that could skip the resume and strand the recording (issue #166).
   *
   * Every pre-finalize stage is bounded. A stage that times out is logged and
   * skipped rather than awaited forever: the server decides whether the segments
   * are complete, and a retryable 409 beats an infinite hang with no finalize.
   */
  stop = async (recordingId?: RecordingId): Promise<Recording> => {
    // An explicit id matters when the runtime is gone and this tab never held
    // the capture: the paused recording is then known only from the server.
    const targetRecordingId =
      recordingId ?? this.state.recordingId ?? this.state.pausedRecording?.id;
    if (!targetRecordingId) {
      throw new Error("No active recording is available to finalize.");
    }

    const hadRuntime = Boolean(this.runtime);
    this.setState({ status: "finalizing", error: null, finalizeRetry: null });

    try {
      const runtime = this.runtime;
      if (runtime) {
        this.setStopStage("stopping-recorder");
        await withStageTimeout(
          "stopping-recorder",
          runtime.recorder.stop({ emitTail: true }),
          RECORDER_STOP_TIMEOUT_MS,
        );

        this.setStopStage("flushing-uploads");
        runtime.uploader.recover();
        try {
          await runtime.uploader.waitForIdle({
            timeoutMs: UPLOAD_FLUSH_TIMEOUT_MS,
          });
        } catch (flushError: unknown) {
          // Keep going: finalize refuses an incomplete upload with a retryable
          // 409, which is a far better outcome than never calling it.
          console.warn(
            "[capture] stop stage \"flushing-uploads\" did not drain cleanly",
            flushError,
          );
        }

        this.setStopStage("releasing-media");
        // Every recorded segment is queued server-side at this point, so stop
        // the microphone/tab capture now instead of keeping the tracks (and
        // the browser recording indicator) live through the finalize retries.
        await this.releaseRuntimeMedia();
      }

      this.setStopStage("finalizing");
      const recording = await this.finalizeRecordingWhenReady(targetRecordingId);
      clearPausedCaptureContext();
      await this.disposeRuntime();
      this.reportCoverageOnStop(recording);
      this.setState({
        status: "idle",
        error: null,
        lastSequence: -1,
        elapsedSeconds: 0,
        recordingId: null,
        pausedRecording: null,
        runtimeActive: false,
        levels: DEFAULT_CAPTURE_LEVELS,
        finalizeRetry: null,
        stopStage: null,
      });
      await this.refreshPausedRecording().catch(() => {});
      this.lifecycle.updateRecordingId(null);
      return recording;

    } catch (error: unknown) {
      const message = formatCaptureError(error);
      console.error(
        `[capture] stop failed during stage "${this.state.stopStage}"`,
        error,
      );
      // Never leave the controller in "finalizing": every transport control is
      // disabled in that state, which previously bricked the UI with no way back.
      // Settling on "paused" matches the server and keeps stop retryable.
      await this.settleAfterFailedStop(targetRecordingId, hadRuntime, message);
      throw new Error(message);
    }
  };

  /**
   * Returns the controller to a state the user can act from after a failed stop.
   *
   * The media tracks are released either way: the recorded audio is already
   * server-side, and holding the microphone open through a failure just leaves
   * the browser recording indicator lit with no way to clear it.
   */
  private settleAfterFailedStop = async (
    recordingId: RecordingId,
    hadRuntime: boolean,
    message: string,
  ) => {
    try {
      await this.releaseRuntimeMedia();
    } catch (releaseError: unknown) {
      console.warn("[capture] failed to release media after a failed stop", releaseError);
    }

    if (hadRuntime) {
      writePausedCaptureContext({
        recordingId,
        lastSequence: this.state.lastSequence,
        persistedAt: Date.now(),
      });
    }

    // The runtime object is kept even though its media is gone, so a retried
    // stop can still flush whatever the uploader is holding. runtimeActive goes
    // false so the transport controls disable and resume re-acquires sources.
    this.setState({
      status: "paused",
      error: message,
      recordingId,
      finalizeRetry: null,
      stopStage: null,
      runtimeActive: false,
      levels: DEFAULT_CAPTURE_LEVELS,
    });
    await this.refreshPausedRecording().catch(() => {});
  };

  private setStopStage = (stage: CaptureStopStage) => {
    console.info(`[capture] stop stage: ${stage}`);
    this.setState({ stopStage: stage });
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
      coverageWarning: null,
      stopStage: null,
    });
    this.lifecycle.updateRecordingId(null);
    await this.refreshPausedRecording().catch(() => {});
  };

  private activateRuntime = async (options: {
    recordingId: RecordingId;
    startSequence: number;
    sources: PickedCaptureSources;
    captureReport: CaptureSourceReportSnapshot;
    elapsedSeconds?: number;
  }) => {
    await this.disposeRuntime();

    const mixer = await createCaptureMixer({
      displayStream: options.sources.displayStream,
      microphoneStream: options.sources.microphoneStream,
      systemGain: this.state.settings.systemGain,
      microphoneGain: this.state.settings.microphoneGain,
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
        this.evaluateCoverage();
      },
      onStalled: async (error) => {
        await this.handleUploaderStalled(options.recordingId, error);
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
      onStall: (info) => {
        this.handleRecorderStall(info);
      },
    });

    recorder.start();
    waveform.start();

    let displayTracksCleanup: (() => void) | undefined;
    if (options.sources.displayStream) {
      const displayStream = options.sources.displayStream;
      const tracks = [...displayStream.getAudioTracks(), ...displayStream.getVideoTracks()];
      let displaySharingEndedHandled = false;

      const handleEnded = () => {
        if (displaySharingEndedHandled) return;

        const currentRecordingId = this.state.recordingId;
        const status = this.state.status;
        if (
          currentRecordingId === options.recordingId &&
          (status === "recording" || status === "paused") &&
          this.runtime
        ) {
          displaySharingEndedHandled = true;
          useNotificationStore.getState().addNotification({
            type: "info",
            message: "Screen sharing ended. The recording has been stopped and saved.",
          });
          this.stop().catch((err) => {
            console.error("[capture] failed to stop recording after sharing ended", err);
          });
        }
      };

      for (const track of tracks) {
        track.addEventListener("ended", handleEnded);
      }

      displayTracksCleanup = () => {
        for (const track of tracks) {
          track.removeEventListener("ended", handleEnded);
        }
      };
    }

    this.runtime = {
      recordingId: options.recordingId,
      sources: options.sources,
      captureReport: options.captureReport,
      mixer,
      recorder,
      uploader,
      waveform,
      displayTracksCleanup,
    };
    this.setState({ runtimeActive: true });
    this.startElapsedTimer(options.elapsedSeconds ?? this.state.elapsedSeconds);
    this.lifecycle.updateRecordingId(options.recordingId);
    this.lifecycle.resetGuard();
  };

  private handleUploaderStalled = async (
    recordingId: RecordingId,
    error: Error,
  ) => {
    this.setState({ error: error.message || formatCaptureError(error) });

    if (!this.runtime || this.state.recordingId !== recordingId) {
      return;
    }

    if (this.state.status === "finalizing") {
      // stop() owns the stalled uploader here: it surfaces the failure and
      // leaves the recording resumable, so do not pause underneath it.
      return;
    }

    // The stalled uploader keeps its queued segments; pause capture so no
    // further audio is recorded until the user resumes and uploads retry.
    try {
      await this.runtime.recorder.pause();
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

    } catch (pauseError: unknown) {
      this.setState({
        status: "error",
        error: formatCaptureError(pauseError),
      });
    }
  };

  /**
   * Compares captured audio against wall-clock recording time.
   *
   * When the browser or the OS suspends the tab, the MediaRecorder stops
   * receiving audio while the recorder still reports "recording" and the elapsed
   * timer keeps counting. Measured at ~52% coverage across a 20s freeze, so the
   * shortfall is real data loss and the only client-side signal is this gap.
   */
  private evaluateCoverage = () => {
    if (this.state.status !== "recording") {
      return;
    }

    const capturedSeconds = sequenceToElapsedSeconds(this.state.lastSequence);
    const elapsedSeconds = this.state.elapsedSeconds;
    const missingSeconds = Math.max(0, elapsedSeconds - capturedSeconds);
    if (
      elapsedSeconds <= 0 ||
      missingSeconds < COVERAGE_WARNING_MIN_MISSING_SECONDS ||
      missingSeconds / elapsedSeconds < COVERAGE_WARNING_MIN_RATIO
    ) {
      return;
    }

    const warning: CaptureCoverageWarning = {
      capturedSeconds,
      elapsedSeconds,
      missingSeconds,
    };
    this.setState({ coverageWarning: warning });

    const now = Date.now();
    if (now - this.lastCoverageNotifiedAt < COVERAGE_WARNING_INTERVAL_MS) {
      return;
    }

    this.lastCoverageNotifiedAt = now;
    const missingMinutes = Math.round(missingSeconds / 60);
    console.warn("[capture] captured audio is behind elapsed time", warning);
    useNotificationStore.getState().addNotification({
      type: "warning",
      message:
        `Nojoin has captured ${Math.round(capturedSeconds / 60)} of ` +
        `${Math.round(elapsedSeconds / 60)} minutes. Around ${missingMinutes} ` +
        "minutes are missing, which usually means this tab was suspended by the " +
        "browser or the device slept. Keep the Nojoin tab open and the device awake.",
    });
  };

  private reportCoverageOnStop = (recording: Recording) => {
    const warning = this.state.coverageWarning;
    if (!warning) {
      return;
    }

    console.warn(
      "[capture] recording finalized with missing audio",
      recording.id,
      warning,
    );
    useNotificationStore.getState().addNotification({
      type: "warning",
      message:
        `This recording captured ${Math.round(warning.capturedSeconds / 60)} of ` +
        `${Math.round(warning.elapsedSeconds / 60)} minutes of the session. The ` +
        "missing audio was not recorded because the tab or device was suspended.",
    });
  };

  private handlePageResume = () => {
    if (!this.runtime) {
      return;
    }

    // Advisory: the watchdog in the recorder is what actually restarts the
    // segment chain. This only records that a thaw happened.
    console.warn("[capture] page resumed from a frozen state during capture");
    this.evaluateCoverage();
  };

  private handleRecorderStall = (info: RecorderStallInfo) => {
    console.warn(
      "[capture] recorder stalled; restarting the segment chain",
      info,
    );
    this.evaluateCoverage();
  };

  private finalizeRecordingWhenReady = async (recordingId: RecordingId) => {
    const maxAttempts = FINALIZE_RETRY_DELAYS_MS.length;
    try {
      for (let attempt = 0; ; attempt += 1) {
        try {
          return await finalizeRecordingCapture(recordingId);

        } catch (error: unknown) {
          const detail = error instanceof AxiosError ? error.response?.data?.detail : null;
          const canRetry =
            error instanceof AxiosError &&
            error.response?.status === 409 &&
            detail === FINALIZE_UPLOAD_IN_PROGRESS_DETAIL &&
            attempt < maxAttempts;

          if (!canRetry) {
            throw error;
          }

          this.setState({
            finalizeRetry: { attempt: attempt + 1, maxAttempts },
          });
          await wait(FINALIZE_RETRY_DELAYS_MS[attempt]);
        }
      }
    } finally {
      this.setState({ finalizeRetry: null });
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

  private releaseRuntimeMedia = async () => {
    const runtime = this.runtime;
    if (!runtime || runtime.mediaReleased) {
      return;
    }

    runtime.mediaReleased = true;
    this.stopElapsedTimer();
    runtime.waveform.stop();
    runtime.displayTracksCleanup?.();
    runtime.sources.release();
    await runtime.mixer.dispose();
    this.setState({ levels: DEFAULT_CAPTURE_LEVELS });
  };

  private disposeRuntime = async () => {
    if (!this.runtime) {
      return;
    }

    const runtime = this.runtime;
    this.stopElapsedTimer();
    await this.releaseRuntimeMedia();
    this.runtime = null;
    runtime.uploader.dispose();
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
