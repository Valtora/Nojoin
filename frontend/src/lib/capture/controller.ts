import { AxiosError } from "axios";

import {
  discardRecordingCapture,
  finalizeRecordingCapture,
  getPausedRecordings,
  getRecording,
  initRecording,
  isActiveRecordingConflictDetail,
  pauseRecordingCapture,
  reportRecordingCaptureSources,
  resumeRecordingCapture,
} from "@/lib/api";
import { useConnectivityStore } from "@/lib/connectivity/monitor";
import { isReachable } from "@/lib/connectivity/reducer";
import { useNotificationStore } from "@/lib/notificationStore";
import { dispatchRecordingRemoved } from "@/lib/recordingEvents";
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
  type CaptureCoverageCause,
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
import { createCaptureWakeLock, type CaptureWakeLock } from "./wakeLock";
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

/** Nominal length of one recorded segment. Matches the recorder's timeslice. */
const CAPTURE_TIMESLICE_SECONDS = 2;

const sequenceToElapsedSeconds = (lastSequence: number) =>
  lastSequence >= 0 ? (lastSequence + 1) * CAPTURE_TIMESLICE_SECONDS : 0;

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
 * Coverage thresholds. Both must be exceeded, so the ordinary few seconds of
 * trailing segment latency stays quiet.
 *
 * The ratio is low because the figure it judges cannot invent a shortfall: the
 * backend's captured-audio total runs slightly high, so a healthy recording
 * reports zero missing rather than a small positive number. It used to be 10%,
 * set to defend against a client-side estimate that under-counted captured
 * audio by about that much and warned on recordings that had lost nothing.
 * Against an honest figure that tolerance only hid real losses: four minutes
 * missing from an 81-minute meeting is 5%, and worth saying.
 */
const COVERAGE_WARNING_MIN_MISSING_SECONDS = 60;

const COVERAGE_WARNING_MIN_RATIO = 0.02;

const COVERAGE_WARNING_INTERVAL_MS = 5 * 60_000;

/**
 * How much further a dismissed shortfall must grow before the warning returns.
 * Enough that ordinary drift cannot undo a dismissal, small enough that a
 * genuinely worsening problem is not silenced for the rest of the meeting.
 */
const COVERAGE_WARNING_REARM_SECONDS = 60;

/**
 * How often to ask the backend how much audio it holds.
 *
 * Only the server knows: it is the one that decodes each segment. Far cheaper
 * than the segment uploads happening alongside it, and slow enough that a
 * shortfall is reported in tens of seconds rather than instantly, which is the
 * right trade for a warning about minutes of lost audio.
 */
const COVERAGE_POLL_INTERVAL_MS = 15_000;

/**
 * How long an outage keeps explaining a shortfall after the server answers
 * again.
 *
 * A shortfall is noticed by a poll that runs every 15 seconds, and it persists
 * while the upload queue drains, so by the time the warning is raised the
 * backend is usually reachable once more. Classifying on the live status alone
 * therefore reported `unknown` for exactly the case the classification exists
 * to name, and told the user to check their tab and their connection for a
 * two-minute stall on the server.
 */
const COVERAGE_RECENT_OUTAGE_MS = 5 * 60_000;

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

  private coveragePollTimerId: ReturnType<typeof setInterval> | null = null;

  /** Server-reported captured audio; null until the first poll succeeds. */
  private capturedAudioSeconds: number | null = null;

  /**
   * Whether the browser has been seen to stop feeding the recorder during this
   * capture. Latched rather than momentary: a freeze is reported once, but the
   * shortfall it caused persists for the rest of the meeting and should keep
   * being attributed to it.
   */
  private sawCaptureFreeze = false;

  /** When the backend was last seen to be unreachable, for the classifier. */
  private lastUnreachableAt: number | null = null;

  private readonly wakeLock: CaptureWakeLock = createCaptureWakeLock();

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
      coverageWarningDismissedAt: null,
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

    this.resetCoverageTracking();
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
    this.resetCoverageTracking();
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
    dispatchRecordingRemoved(targetRecordingId);
    await this.refreshPausedRecording().catch(() => {});
  };

  /**
   * Discards a paused recording this tab lost before it banked any audio.
   *
   * A document unload during capture is guarded: `handleGuardedExit` pauses the
   * recording so the audio already uploaded survives. That is right in the
   * middle of a meeting and wrong immediately after one starts, which is where
   * it kept landing. A `router.push` degrades to a full-document navigation
   * whenever Next.js finds the tab running a different build from the server
   * (any upgrade under an open tab does it), and the app performs exactly such
   * a push a second after `start()` resolves. The guard then paused a recording
   * that was seconds old and the reloaded page opened the resume-or-discard
   * modal over a meeting the user had only just started.
   *
   * The guard itself has to stay: `beforeunload`/`pagehide` fire only for a real
   * unload, so skipping the pause would strand the recording in `UPLOADING`
   * with no client, where `POST /init` rejects every later start with
   * `active_recording_exists` and no modal offers a way out.
   *
   * So the pause stands and the prompt goes. `lastSequence` in the persisted
   * context is the uploader's own count of segments the server acknowledged;
   * below zero means nothing was banked and there is nothing for the user to
   * decide about. The context is per-tab `sessionStorage`, so this only ever
   * acts on a capture this tab owned and lost. A recording paused any other way
   * still goes to the modal.
   */
  discardEmptyInterruptedRecording = async (): Promise<boolean> => {
    const paused = this.state.pausedRecording;
    if (!paused || this.runtime) {
      return false;
    }

    const context = readPausedCaptureContext();
    if (
      !context ||
      context.recordingId !== paused.id ||
      context.lastSequence >= 0
    ) {
      return false;
    }

    await this.cancel(paused.id);
    useNotificationStore.getState().addNotification({
      type: "warning",
      message:
        "The last recording was interrupted before any audio was captured, " +
        "so it was discarded. Start the meeting again to record.",
    });
    return true;
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
   * Asks the backend how much audio it holds, and re-judges coverage.
   *
   * The client cannot answer this itself. It used to try, multiplying the last
   * sequence number by the timeslice, but a segment carries slightly more than
   * the nominal timeslice because each roll flushes whatever accumulated while
   * it was stopping. That under-counted captured audio by around 10% over an
   * hour and reported minutes missing from recordings that had lost nothing.
   */
  private pollCapturedAudio = async () => {
    const recordingId = this.state.recordingId;
    if (!recordingId || this.state.status !== "recording") {
      return;
    }

    try {
      const recording = await getRecording(recordingId);
      const captured = recording.captured_audio_seconds;
      if (typeof captured !== "number") {
        return;
      }
      this.capturedAudioSeconds = captured;
      this.evaluateCoverage();
    } catch {
      // A failed poll is not evidence of anything. The connectivity monitor
      // already owns "is the backend reachable"; this one just goes quiet.
    }
  };

  /**
   * Classifies a shortfall, so the warning can say what to do about it.
   *
   * Deliberately conservative about blaming the tab. A page-freeze event or a
   * recorder-watchdog stall is direct evidence the browser stopped feeding the
   * recorder; an unreachable backend is direct evidence of the opposite. With
   * neither, `unknown` is honest, and the copy describes the gap without
   * diagnosing it. Blaming tab suspension by default is what sent someone to
   * check a Chrome setting during an outage that was entirely server-side.
   */
  private classifyCoverageCause = (): CaptureCoverageCause => {
    if (!isReachable(useConnectivityStore.getState().status)) {
      this.lastUnreachableAt = Date.now();
      return "backend-unreachable";
    }
    if (this.sawCaptureFreeze) {
      return "tab-suspended";
    }
    // An outage that has just ended still explains the gap it left behind. It
    // ranks below a freeze, because a freeze is direct evidence the browser
    // stopped recording while an outage is evidence it did not.
    if (
      this.lastUnreachableAt !== null &&
      Date.now() - this.lastUnreachableAt <= COVERAGE_RECENT_OUTAGE_MS
    ) {
      return "backend-unreachable";
    }
    return "unknown";
  };

  private coverageMessage = (warning: CaptureCoverageWarning): string => {
    const captured = Math.round(warning.capturedSeconds / 60);
    const elapsed = Math.round(warning.elapsedSeconds / 60);
    const missing = Math.round(warning.missingSeconds / 60);
    const queued = Math.round(warning.queuedSeconds / 60);
    const headline =
      `Nojoin has ${captured} of ${elapsed} minutes of audio, so around ` +
      `${missing} minutes are missing. ` +
      (queued >= 1
        ? `A further ${queued} minutes are recorded and waiting to upload. `
        : "");

    if (warning.cause === "backend-unreachable") {
      return (
        headline +
        "Nojoin cannot reach the server at the moment. Recording is continuing, " +
        "and queued audio uploads when the connection returns."
      );
    }
    if (warning.cause === "tab-suspended") {
      return (
        headline +
        "This tab was suspended by the browser or the device slept, and audio " +
        "from that period was not recorded. Keep the Nojoin tab open and the " +
        "device awake."
      );
    }
    return (
      headline +
      "Keep the Nojoin tab open and the device awake, and check your connection " +
      "to the Nojoin server."
    );
  };

  /**
   * Recorded audio the browser is still holding, estimated from the queue.
   *
   * Approximate for the same reason the captured figure is measured rather than
   * derived: a segment carries a little more than the nominal timeslice. It is
   * only ever subtracted from a shortfall, so the bias makes the warning
   * slightly readier to fire, never readier to stay silent.
   */
  private queuedAudioSeconds = (): number => {
    const pending = this.runtime?.uploader.pendingSegmentCount() ?? 0;
    return pending * CAPTURE_TIMESLICE_SECONDS;
  };

  /**
   * Compares the audio the server holds against wall-clock recording time.
   */
  private evaluateCoverage = () => {
    if (this.state.status !== "recording") {
      return;
    }

    const capturedSeconds = this.capturedAudioSeconds;
    // Nothing to compare against until the first segment has been transcoded.
    // Silence beats guessing: guessing is what produced the false warnings.
    if (capturedSeconds === null) {
      return;
    }

    const elapsedSeconds = this.state.elapsedSeconds;
    // What the browser is still holding is not missing. Subtracting the queue
    // is the difference between "the server stopped answering and your audio is
    // waiting" and "your meeting is being lost", which are the two readings of
    // the same arithmetic and only one of them is true during an outage.
    const queuedSeconds = this.queuedAudioSeconds();
    const missingSeconds = Math.max(
      0,
      elapsedSeconds - capturedSeconds - queuedSeconds,
    );
    if (
      elapsedSeconds <= 0 ||
      missingSeconds < COVERAGE_WARNING_MIN_MISSING_SECONDS ||
      missingSeconds / elapsedSeconds < COVERAGE_WARNING_MIN_RATIO
    ) {
      return;
    }

    const dismissedAt = this.state.coverageWarningDismissedAt;
    if (
      dismissedAt !== null &&
      missingSeconds < dismissedAt + COVERAGE_WARNING_REARM_SECONDS
    ) {
      return;
    }

    const warning: CaptureCoverageWarning = {
      capturedSeconds,
      elapsedSeconds,
      missingSeconds,
      queuedSeconds,
      cause: this.classifyCoverageCause(),
    };
    this.setState({ coverageWarning: warning, coverageWarningDismissedAt: null });

    const now = Date.now();
    if (now - this.lastCoverageNotifiedAt < COVERAGE_WARNING_INTERVAL_MS) {
      return;
    }

    this.lastCoverageNotifiedAt = now;
    console.warn("[capture] captured audio is behind elapsed time", warning);
    useNotificationStore.getState().addNotification({
      type: "warning",
      message: this.coverageMessage(warning),
    });
  };

  /**
   * Dismisses the coverage warning, remembering the shortfall it was showing.
   *
   * The warning used to have no way out: once raised it stayed for the rest of
   * the meeting, including when its diagnosis was wrong.
   */
  dismissCoverageWarning = () => {
    const warning = this.state.coverageWarning;
    if (!warning) {
      return;
    }

    this.setState({
      coverageWarning: null,
      coverageWarningDismissedAt: warning.missingSeconds,
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
    const captured = Math.round(warning.capturedSeconds / 60);
    const elapsed = Math.round(warning.elapsedSeconds / 60);
    useNotificationStore.getState().addNotification({
      type: "warning",
      message:
        `This recording holds ${captured} of ${elapsed} minutes of the session. ` +
        (warning.cause === "backend-unreachable"
          ? "Nojoin lost contact with the server during the meeting; anything " +
            "that finished uploading has been kept."
          : "The missing audio was not recorded."),
    });
  };

  private handlePageResume = () => {
    if (!this.runtime) {
      return;
    }

    // Advisory: the watchdog in the recorder is what actually restarts the
    // segment chain. This only records that a thaw happened, which is the
    // evidence that lets a shortfall be blamed on the tab rather than guessed at.
    console.warn("[capture] page resumed from a frozen state during capture");
    this.sawCaptureFreeze = true;
    this.evaluateCoverage();
  };

  private handleRecorderStall = (info: RecorderStallInfo) => {
    console.warn(
      "[capture] recorder stalled; restarting the segment chain",
      info,
    );
    // The recorder went quiet while still recording, which no network problem
    // can cause: segment production does not wait on uploads.
    this.sawCaptureFreeze = true;
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

    // Written before anything is awaited. This runs during an unload, so the
    // document can be torn down at the first yield and everything after it may
    // never execute; the context used to be written in the `finally`, three
    // awaits later. It is the only record of what this tab was capturing, and
    // discardEmptyInterruptedRecording needs its sequence count on the next
    // load to tell an interruption worth resuming from one worth clearing.
    writePausedCaptureContext({
      recordingId,
      lastSequence,
      persistedAt: Date.now(),
    });

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

  /** Clears everything tracked per-recording, so nothing carries over. */
  private resetCoverageTracking() {
    this.lastCoverageNotifiedAt = 0;
    this.capturedAudioSeconds = null;
    this.sawCaptureFreeze = false;
  }

  private startCoveragePoll() {
    this.stopCoveragePoll();
    // Fire once immediately so a resumed recording is judged against the audio
    // already banked, rather than waiting a full interval to notice a shortfall
    // that predates this session.
    void this.pollCapturedAudio();
    this.coveragePollTimerId = setInterval(() => {
      void this.pollCapturedAudio();
    }, COVERAGE_POLL_INTERVAL_MS);
  }

  private stopCoveragePoll() {
    if (this.coveragePollTimerId) {
      clearInterval(this.coveragePollTimerId);
      this.coveragePollTimerId = null;
    }
  }

  private startElapsedTimer(initialElapsedSeconds: number) {
    this.stopElapsedTimer();
    this.startCoveragePoll();
    void this.wakeLock.acquire();
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
    this.stopCoveragePoll();
    void this.wakeLock.release();
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
