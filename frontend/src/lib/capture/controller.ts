import { AxiosError } from "axios";

import {
  discardRecordingCapture,
  finalizeRecordingCapture,
  getRecording,
  getUnfinishedCaptures,
  getUserMe,
  initRecording,
  isActiveRecordingConflictDetail,
  isCaptureClosedError,
  isCaptureEndedError,
  pauseRecordingCapture,
  reportRecordingCaptureSources,
  resumeRecordingCapture,
} from "@/lib/api";
import { useConnectivityStore } from "@/lib/connectivity/monitor";
import { isReachable } from "@/lib/connectivity/reducer";
import { useNotificationStore } from "@/lib/notificationStore";
import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import { dispatchRecordingRemoved } from "@/lib/recordingEvents";
import { getErrorStatus } from "@/lib/errors";
import {
  holdSignInInPlace,
  noteSignedIn,
  retryAfterSignIn,
  useSessionStore,
} from "@/lib/session";
import { RecordingStatus, type Recording, type RecordingId } from "@/types";

import { holdCaptureLock } from "./captureLock";
import { findAttentionRecordings, selectRecoveryOffer, type RecoveryRequest } from "./recovery";
import { detectCaptureSupport } from "./featureDetect";
import {
  createLeftOutAudioStore,
  describeAudioLength,
  offerLeftOutAudio,
  type LeftOutAudioStore,
} from "./leftOutAudio";
import { CaptureLifecycle, sendPauseBeacon } from "./lifecycle";
import { createCaptureMixer, type CaptureMixer } from "./mixer";
import { FLAC_MIME_TYPE } from "./pcm/flacEncoder";
import { joinFlacSegments } from "./pcm/flacJoin";
import { pickCaptureSource, PickSourceError, type PickedCaptureSources } from "./pickSource";
import {
  assertMicrophoneResumeCurrent,
  reconnectPausedMicrophone,
  releasePausedMicrophone,
  type PausedMicrophoneRuntime,
} from "./pausedMicrophone";
import {
  CAPTURE_SEGMENT_SECONDS,
  createBrowserRecorder,
  type BrowserRecorder,
  type RecorderStallInfo,
} from "./recorder";
import {
  contiguousSegmentsAfter,
  createSegmentStore,
  storedSegmentEpoch,
  type SegmentStore,
  type StoredSegment,
} from "./segmentStore";
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

interface ActiveRuntime extends PausedMicrophoneRuntime {
  recordingId: RecordingId;
  /**
   * The capture epoch this tab holds, from init or resume. Every request it
   * makes for its capture carries it, and the server refuses them once the
   * capture is resumed in another tab or on another device (issue #570).
   */
  epoch: number;
  /** The account the capture belongs to, when the page knew it at the start. */
  ownerId?: number;
  sources: PickedCaptureSources;
  captureReport: CaptureSourceReportSnapshot;
  mixer: CaptureMixer;
  recorder: BrowserRecorder;
  uploader: SegmentUploader;
  waveform: WaveformMonitor;
  displayTracksCleanup?: () => void;
  mediaReleased?: boolean;
  /** Tells other tabs of this browser that this capture is live. */
  releaseCaptureLock: () => void;
}

/**
 * A pause this tab has made that the server has not received.
 *
 * The recorder pauses at once. Until the server is told, it holds the capture
 * as uploading and, after two minutes, offers it to other tabs and devices as
 * interrupted, so the pause is sent when the API next answers and before any
 * resume. It carries this tab's epoch, so it is refused rather than pausing a
 * capture someone has resumed elsewhere meanwhile.
 */
interface UnconfirmedPause {
  recordingId: RecordingId;
  /** When the last attempt failed. Only a later answer from the API retries it. */
  failedAt: number;
  /** Attempts that have failed in a row, which set how long the next one waits. */
  failures: number;
}

const owePause = (recordingId: RecordingId): UnconfirmedPause => ({
  recordingId,
  failedAt: Number.NEGATIVE_INFINITY,
  failures: 0,
});

/**
 * How long after a failed attempt the owed pause waits before it is sent again,
 * once the API has answered since.
 *
 * The first retry goes on the first answer, which after an outage is the moment
 * the API is back. Later ones back off, doubling from 2 s to 30 s: when only the
 * pause fails while everything else gets through, a blocked path or a broken
 * proxy say, each failure brings a connectivity probe forward and its answer
 * would otherwise send the pause again about twice a second (issue #599).
 */
const OWED_PAUSE_RETRY_FIRST_BACKOFF_MS = 2_000;

const OWED_PAUSE_RETRY_MAX_BACKOFF_MS = 30_000;

const owedPauseRetryDelayMs = (failures: number) =>
  failures <= 1
    ? 0
    : Math.min(
        OWED_PAUSE_RETRY_MAX_BACKOFF_MS,
        OWED_PAUSE_RETRY_FIRST_BACKOFF_MS * 2 ** (failures - 2),
      );

/**
 * Length of one recorded segment. Exact rather than nominal: the recorder cuts
 * segments by counting frames, so only a segment ended by a pause or a stop is
 * shorter.
 */
const CAPTURE_TIMESLICE_SECONDS = CAPTURE_SEGMENT_SECONDS;

const EMPTY_UPLOAD_QUEUE = { segments: 0, seconds: 0 };

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
 * reports zero missing rather than a small positive number. A higher tolerance
 * only hides real losses: four minutes missing from an 81-minute meeting is 5%,
 * and worth saying.
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
 * would report `unknown` for exactly the case the classification exists to
 * name, and send the user to check their tab and connection for a two-minute
 * stall on the server.
 */
const COVERAGE_RECENT_OUTAGE_MS = 5 * 60_000;

/**
 * How often a tab with no capture of its own looks again at the user's
 * unfinished captures while one exists.
 *
 * A capture whose tab closed without pausing it becomes something to offer
 * only once the server judges it stalled, two minutes after its last upload,
 * and a decision offered here goes stale when another tab or device acts on
 * it. Looking again keeps the resume-or-discard dialog in step with both,
 * without a reload (issue #519).
 */
const CAPTURE_RECHECK_INTERVAL_MS = 30_000;

/**
 * A 404 for this tab's capture once `/users/me` has named the capture's own
 * account and the recording still answers 404: it was discarded elsewhere
 * (issue #602). A 404 alone can mean the browser is signed in as another
 * account, which must wait for the owner rather than stand down.
 */
class CaptureDiscardedError extends Error {
  constructor() {
    super("This recording was discarded in another tab or on another device.");
    this.name = "CaptureDiscardedError";
  }
}

/** Whether the capture has ended for this tab: taken over, stopped or discarded. */
const isEndedForThisTab = (error: unknown) =>
  isCaptureEndedError(error) || error instanceof CaptureDiscardedError;

/**
 * What a tab says once the server refuses it because its capture ended for it:
 * resumed elsewhere, stopped or discarded.
 */
const endedElsewhereMessage = (error: unknown, queuedSeconds: number) =>
  (isCaptureClosedError(error)
    ? "This recording has already been stopped and sent for processing, so " +
      "this tab is no longer recording it."
    : error instanceof CaptureDiscardedError
      ? "This recording was discarded in another tab or on another device, so " +
        "this tab is no longer recording it."
      : "This recording continued in another tab or on another device, so this " +
        "tab has stopped recording it.") +
  (queuedSeconds > 0
    ? ` About ${describeAudioLength(queuedSeconds)} of audio this tab recorded ` +
      "had not uploaded and is not in the recording."
    : "");

/**
 * The epoch under which audio this browser kept continues the capture.
 *
 * A tab resuming its own capture keeps its epoch. A takeover is issued the
 * next one, and only audio kept under the epoch just before it follows what the
 * server holds: anything older was queued by a tab whose capture someone else
 * then carried on, and would land out of order.
 */
const continuingEpoch = (
  response: { capture_epoch: number },
  heldEpoch: number | undefined,
) => heldEpoch ?? response.capture_epoch - 1;

/**
 * Whether the API itself answered a failed request, as opposed to the request
 * being lost or a gateway answering for an API that is down. The same split
 * the API client reports to the connectivity monitor.
 */
const answeredByServer = (error: unknown) => {
  if (!(error instanceof AxiosError) || !error.response) {
    return false;
  }
  const status = error.response.status;
  return status !== 502 && status !== 503 && status !== 504;
};

/** The account this page is signed in as, once the server has said. */
const signedInAccountId = () => useSessionStore.getState().userId ?? undefined;

/**
 * Whether the server's copy of a recording takes no more capture audio. An
 * unknown status counts as unfinished, so kept audio is never cleared on a
 * guess.
 */
const isFinishedCapture = (recording: Pick<Recording, "status">) =>
  recording.status !== undefined &&
  recording.status !== RecordingStatus.UPLOADING &&
  recording.status !== RecordingStatus.PAUSED;

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

  private readonly segmentStore: SegmentStore = createSegmentStore();

  /** Audio left out of its recording, kept for its owner to download (issue #605). */
  private readonly leftOutAudio: LeftOutAudioStore = createLeftOutAudioStore();

  /**
   * Recordings whose kept audio is being set aside to download, so a check
   * for finished captures leaves it alone and a resume reads the store after.
   */
  private readonly settingAside = new Map<RecordingId, Promise<unknown>>();

  /** Offers of left-out audio closed on this page. They return on the next. */
  private readonly closedLeftOutOffers = new Set<string>();

  private captureRecheckTimerId: ReturnType<typeof setInterval> | null = null;

  /** Whether kept audio is being checked against the server, so one check runs. */
  private checkingKeptAudio = false;

  /**
   * Recordings this page has already asked the server about without a clear
   * answer: a 404 for audio kept without its owner, or while signed in as
   * another account. Asked again on the next page load, not every 30 seconds.
   */
  private readonly keptAudioUndecided = new Set<RecordingId>();

  /**
   * Bumped by every user action on a capture, so a check that was already in
   * flight cannot put back a recording the action has just resolved.
   */
  private captureActionTicket = 0;

  private navigationTicket = 0;

  private unconfirmedPause: UnconfirmedPause | null = null;

  /** The attempt to send the unconfirmed pause under way, so only one runs. */
  private serverPauseAttempt: Promise<unknown> | null = null;

  private stopWatchingForServer: (() => void) | null = null;

  /** The owed pause's next attempt, held back until its delay has passed. */
  private owedPauseRetryTimerId: ReturnType<typeof setTimeout> | null = null;

  private microphoneResumePending = false;

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
      attentionRecordings: [],
      runtimeActive: false,
      settings: readCaptureSettings(),
      finalizeRetry: null,
      stopStage: null,
      coverageWarning: null,
      coverageWarningDismissedAt: null,
      uploadQueue: EMPTY_UPLOAD_QUEUE,
    };

    this.lifecycle = new CaptureLifecycle({
      getRecordingId: () => this.state.recordingId,
      shouldGuardExit: () => Boolean(this.runtime),
      onGuardedExit: (request) => this.handleGuardedExit(request),
      onPageResume: () => this.handlePageResume(),
    });
  }

  getState = () => this.state;

  /** Includes a failed stop's retained runtime, even after its media is released. */
  getOwnedRecordingId = () => this.runtime?.recordingId ?? null;

  /** Exactly one recovery check per recording-page entry, never on later polls. */
  openRecording = async (recordingId: RecordingId | null) => {
    const ticket = ++this.navigationTicket;
    // No offer over this tab's own capture action (notably a permission prompt
    // during Resume); that action looks again itself when it ends.
    if (!recordingId || this.runtime || this.state.status === "starting" ||
      this.state.status === "finalizing") return;
    await this.refreshPausedRecording({
      recordingId,
      isCurrent: () => ticket === this.navigationTicket,
    });
  };

  /**
   * The shared tab, window or screen's video track while this tab captures
   * `recordingId`, for taking a still of it (Captures, #653). Null for a
   * microphone-only capture, another recording, or once media is released.
   * A caller must never stop it: ending the track ends the share, and that
   * stops the recording.
   */
  displayVideoTrack = (recordingId: RecordingId): MediaStreamTrack | null => {
    const runtime = this.runtime;
    if (!runtime || runtime.recordingId !== recordingId || runtime.mediaReleased) {
      return null;
    }
    const track = runtime.sources.displayStream?.getVideoTracks?.()[0];
    return track && track.readyState === "live" ? track : null;
  };

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
    this.captureActionTicket += 1;
    this.navigationTicket += 1;
    this.lifecycle.detach();
    this.stopCaptureRecheck();
    await this.disposeRuntime();
  };

  /**
   * Finds the capture the user must decide about before starting anything new.
   *
   * Discovery updates badges without opening a dialog. Navigation and explicit
   * actions can offer a capture unless another tab of this browser holds it.
   * A capture still marked as
   * uploading counts once the server judges it stalled:
   * its tab closed without the pause arriving, because it was signed out or
   * offline at the time (issue #519).
   * One this tab runs, or any tab of this browser holds, is live however quiet
   * it looks, since that tab may only be waiting for a sign-in or a connection.
   */
  refreshPausedRecording = async (options: RecoveryRequest = {}) => {
    const explicit = Boolean(options.onDemand || options.recordingId);
    if (!explicit && (this.state.status === "starting" || this.state.status === "finalizing")) {
      return this.state.pausedRecording;
    }
    const ticket = this.captureActionTicket;
    const unfinished = await getUnfinishedCaptures();
    const captures = unfinished.filter(isLiveCaptureInProgress);
    const attentionRecordings = await findAttentionRecordings(captures, this.getOwnedRecordingId());
    if (ticket !== this.captureActionTicket || options.isCurrent?.() === false) {
      // An action began meanwhile and will look again itself when it ends.
      return this.state.pausedRecording;
    }
    const ownedPause = captures.find((recording) =>
      recording.id === this.getOwnedRecordingId() && recording.status === RecordingStatus.PAUSED);
    // Read the current offer at completion, so a passive check that finishes
    // after an explicit one keeps what it offered.
    const decision = ownedPause ??
      selectRecoveryOffer(attentionRecordings, this.state.pausedRecording?.id ?? null, options);
    void this.settleAudioKeptForFinishedCaptures(unfinished);
    this.setState({ pausedRecording: decision, attentionRecordings });
    if (this.runtime || captures.length === 0) {
      this.stopCaptureRecheck();
    } else {
      this.startCaptureRecheck();
    }
    return decision;
  };

  private startCaptureRecheck() {
    if (this.captureRecheckTimerId) {
      return;
    }
    this.captureRecheckTimerId = setInterval(() => {
      // A start, resume or stop under way looks again itself when it ends.
      if (this.state.status === "starting" || this.state.status === "finalizing") {
        return;
      }
      void this.refreshPausedRecording().catch(() => {});
    }, CAPTURE_RECHECK_INTERVAL_MS);
  }

  private stopCaptureRecheck() {
    if (this.captureRecheckTimerId) {
      clearInterval(this.captureRecheckTimerId);
      this.captureRecheckTimerId = null;
    }
  }

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
    // The recovery check awaits, so Start holds "starting" before it: a second
    // click meanwhile must not start another capture.
    if (this.runtime || this.state.status === "starting" || this.state.status === "finalizing") return null;
    const previousStatus = this.state.status;
    this.setState({ status: "starting", error: null });
    try {
      if (await this.refreshPausedRecording({ onDemand: true })) {
        this.setState({ status: previousStatus });
        return null;
      }
      return await this.startCapture(name);
    } catch (error) {
      if (this.getState().status === "starting") this.setState({ status: previousStatus });
      throw error;
    }
  };

  private startCapture = async (name: string | undefined): Promise<StartCaptureResponse> => {
    this.captureActionTicket += 1;
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
        await this.refreshPausedRecording({ onDemand: true }).catch(() => {});
        this.setState({
          status: this.state.pausedRecording ? "paused" : "idle",
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
        epoch: initResponse.capture_epoch,
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

  /**
   * Pauses the capture in this tab, then on the server.
   *
   * The tab reads paused as soon as its recorder stops taking audio, whatever
   * becomes of the request: a tab that reads recording while nothing is
   * recorded loses whatever the user says next (issue #582). The server is
   * told the way it is after an upload stall, once the queued audio has gone,
   * so a pause that does not reach it is sent again when the API next answers
   * and before any resume.
   *
   * Resolves once the server has the pause, or owes it with a warning when the
   * server could not be reached. Rejects when the server refused it, with the
   * tab still paused, or when the capture ended elsewhere.
   */
  pause = async () => {
    const runtime = this.runtime;
    const recordingId = this.state.recordingId;
    if (!runtime || !recordingId || this.state.status !== "recording") {
      return;
    }

    await runtime.recorder.pause();
    // An upload stall, a stop, an unload or a takeover may have acted while
    // the recorder paused, and owns the capture from here.
    if (this.runtime !== runtime || this.state.status !== "recording") {
      return;
    }
    releasePausedMicrophone(runtime, this.state.settings.releaseMicrophoneOnPause);
    this.stopElapsedTimer();
    writePausedCaptureContext({
      recordingId,
      lastSequence: this.state.lastSequence,
      persistedAt: Date.now(),
    });
    this.setState({ status: "paused" });
    this.unconfirmedPause = owePause(recordingId);

    const error = await this.sendServerPause({ drainFirst: true });
    if (!error) {
      return;
    }
    if (isEndedForThisTab(error)) {
      throw await this.ifEndedElsewhere(error, runtime);
    }
    if (this.unconfirmedPause?.recordingId === recordingId) {
      useNotificationStore.getState().addNotification({
        type: "warning",
        message:
          "Recording paused. Nojoin could not reach the server to tell it, " +
          "and will once the connection returns.",
      });
      return;
    }
    throw new Error(formatCaptureError(error));
  };

  resume = async (recordingId?: RecordingId): Promise<StartCaptureResult> => {
    const targetRecordingId =
      recordingId ?? this.state.pausedRecording?.id ?? this.state.recordingId;
    if (!targetRecordingId) {
      throw new Error("No paused recording is available to resume.");
    }
    if (this.microphoneResumePending) {
      throw new Error("The recording is still resuming. Wait for this Resume attempt to finish.");
    }
    this.captureActionTicket += 1;
    const ticket = this.captureActionTicket;

    // Reusing the existing tracks is only valid while they are still live. After
    // a failed stop the runtime survives with its media released, and resuming
    // onto those dead tracks would record silence.
    if (
      this.runtime &&
      this.runtime.recordingId === targetRecordingId &&
      !this.runtime.mediaReleased &&
      this.state.status === "paused"
    ) {
      return this.resumeRuntime(this.runtime, targetRecordingId, ticket);
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

    // After a failed stop this tab still holds the capture, with its media
    // released. It resumes as the holder, so its own uploads, which may still
    // be sending, are not refused as a takeover's would be.
    const heldRuntime = this.runtime?.recordingId === targetRecordingId ? this.runtime : null;
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

      resumeResponse = await this.forCapture(
        targetRecordingId,
        heldRuntime ? heldRuntime.ownerId : signedInAccountId(),
        () => resumeRecordingCapture(targetRecordingId, { epoch: heldRuntime?.epoch }),
      );
      // Audio a previous page recorded but never uploaded goes first, and new
      // audio continues numbering after it.
      const restoredSegments = await this.loadQueuedSegments(
        targetRecordingId,
        resumeResponse.last_sequence,
        continuingEpoch(resumeResponse, heldRuntime?.epoch),
      );
      const lastRecordedSequence =
        restoredSegments.at(-1)?.sequence ?? resumeResponse.last_sequence;
      await this.activateRuntime({
        recordingId: targetRecordingId,
        epoch: resumeResponse.capture_epoch,
        startSequence: lastRecordedSequence + 1,
        uploadFromSequence: resumeResponse.last_sequence + 1,
        restoredSegments,
        sources,
        captureReport: sources.captureReport,
        elapsedSeconds: sequenceToElapsedSeconds(lastRecordedSequence),
      });
      const captureReport = sources.captureReport;
      sources = null;
      clearPausedCaptureContext();
      this.setState({
        status: "recording",
        recordingId: targetRecordingId,
        lastSequence: resumeResponse.last_sequence,
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
      if (isEndedForThisTab(error)) {
        throw await this.ifEndedElsewhere(error, heldRuntime);
      }
      if (resumeResponse) {
        const epoch = resumeResponse.capture_epoch;
        await pauseRecordingCapture(targetRecordingId, { epoch }).catch(() => {});
      }
      this.setState({
        status: "error",
        error: formatCaptureError(error),
      });
      // A stalled capture whose own tab came back is refused, and is no longer
      // the user's to decide about here.
      await this.refreshPausedRecording().catch(() => {});
      throw error;
    }
  };

  private resumeRuntime = async (runtime: ActiveRuntime, recordingId: RecordingId, ticket: number) => {
    const released = Boolean(runtime.microphoneReleased);
    const isCurrent = () => this.runtime === runtime && !runtime.mediaReleased &&
      this.state.status === "paused" && ticket === this.captureActionTicket;
    let usedDefault = false;
    let response;
    this.microphoneResumePending = true;
    try {
      await this.confirmUnconfirmedPause();
      if (released) {
        assertMicrophoneResumeCurrent(isCurrent);
        usedDefault = await reconnectPausedMicrophone(runtime, this.state.settings, isCurrent);
      }
      if (released) assertMicrophoneResumeCurrent(isCurrent);
      // Keep this tab's epoch: resuming elsewhere must still refuse this holder.
      response = await retryAfterSignIn(() =>
        this.forCapture(recordingId, runtime.ownerId, () =>
          resumeRecordingCapture(recordingId, { epoch: runtime.epoch }),
        ),
      );
      assertMicrophoneResumeCurrent(isCurrent);
      // A stalled uploader retries its retained queue before new audio.
      runtime.uploader.recover();
      // Not awaited: a context the browser keeps interrupted must not hold
      // Resume. The recorder's flush gives it a moment to start rendering.
      void runtime.mixer.resumeContext().catch(() => {});
      await runtime.recorder.resume();
      assertMicrophoneResumeCurrent(isCurrent);
    } catch (error: unknown) {
      if (isCurrent()) {
        if (released) releasePausedMicrophone(runtime, true);
        this.setState({ status: "paused", error: formatCaptureError(error) });
      }
      throw await this.ifEndedElsewhere(error, runtime);
    } finally {
      this.microphoneResumePending = false;
    }
    this.startElapsedTimer(this.state.elapsedSeconds);
    clearPausedCaptureContext();
    this.setState({
      status: "recording",
      recordingId,
      lastSequence: Math.max(response.last_sequence, this.state.lastSequence),
      error: null,
    });
    this.warnMicrophoneFallback(usedDefault);
    await this.reportCaptureSourceAttempt(
      recordingId,
      buildCaptureSourceReportPayload(runtime.captureReport, {
        attempt_kind: "resume",
        outcome: "success",
        notes: released ? [] : ["reused_existing_browser_tracks"],
      }),
    );
    await this.refreshPausedRecording().catch(() => {});
    return { recordingId, resumed: true };
  };

  private warnMicrophoneFallback = (usedDefault: boolean) => {
    if (usedDefault) {
      useNotificationStore.getState().addNotification({
        type: "warning",
        message: "The selected microphone is no longer listed by the browser. Using the system default microphone. Check the waveform before continuing.",
      });
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
    this.assertCaptureControlAllowed(targetRecordingId);
    this.captureActionTicket += 1;

    const hadRuntime = Boolean(this.runtime);
    const runtime = this.runtime;
    // Finalize accepts an uploading recording, and a stop that fails owes the
    // pause again.
    this.forgetUnconfirmedPause();
    this.setState({ status: "finalizing", error: null, finalizeRetry: null });

    try {
      if (runtime) {
        this.setStopStage("stopping-recorder");
        await withStageTimeout(
          "stopping-recorder",
          runtime.recorder.stop({ emitTail: true }),
          RECORDER_STOP_TIMEOUT_MS,
        );

        this.setStopStage("releasing-media");
        // Once the recorder has stopped, every recorded segment is in the
        // uploader's queue, so the microphone and shared tab are no longer
        // needed. Release them before the upload rather than after it: a long
        // queue takes minutes to send, and a signed-out tab waits for the user
        // to sign back in first (issue #502), and the browser's recording
        // indicator should not stay lit through either. Stopping the elapsed
        // timer here also keeps that wait out of the elapsed time sent with
        // finalize.
        await this.releaseRuntimeMedia();

        this.setStopStage("flushing-uploads");
        runtime.uploader.recover();
        try {
          await runtime.uploader.waitForIdle({
            timeoutMs: UPLOAD_FLUSH_TIMEOUT_MS,
          });
        } catch (flushError: unknown) {
          // Nothing of this tab's can finish a capture carried on or stopped
          // elsewhere.
          if (isEndedForThisTab(flushError)) {
            throw flushError;
          }
          // Keep going: finalize refuses an incomplete upload with a retryable
          // 409, which is a far better outcome than never calling it.
          console.warn(
            "[capture] stop stage \"flushing-uploads\" did not drain cleanly",
            flushError,
          );
        }
      } else {
        await this.uploadStoredSegments(targetRecordingId);
      }

      this.setStopStage("finalizing");
      const ownCapture = runtime?.recordingId === targetRecordingId ? runtime : null;
      const recording = await this.finalizeRecordingWhenReady(
        targetRecordingId,
        hadRuntime ? this.state.elapsedSeconds : undefined,
        ownCapture?.epoch,
        ownCapture ? ownCapture.ownerId : signedInAccountId(),
      );
      clearPausedCaptureContext();
      await this.segmentStore.clear(targetRecordingId).catch(() => {});
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
      if (isEndedForThisTab(error)) {
        throw await this.ifEndedElsewhere(error, runtime);
      }
      const message = formatCaptureError(error);
      console.error(
        `[capture] stop failed during stage "${this.state.stopStage}"`,
        error,
      );
      // Never leave the controller in "finalizing": every transport control is
      // disabled in that state, leaving the UI with no way back. Settling on
      // "paused" matches the server and keeps stop retryable.
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
   *
   * A stop never pauses on the server, so after a failed one the server can
   * still hold this tab's capture as uploading. It is told the capture is
   * paused, as after an upload stall, so the recording is offered for resume,
   * stop or discard rather than read as still in progress.
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
      this.unconfirmedPause = owePause(recordingId);
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
    void this.sendServerPause({ drainFirst: false });
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
    this.assertCaptureControlAllowed(targetRecordingId);
    this.captureActionTicket += 1;

    // A tab whose capture continued elsewhere must not delete it from under
    // the tab now running it, nor delete one stopped elsewhere and now being
    // processed. A discard from a dialog or the list sends none.
    const ownCapture = this.runtime?.recordingId === targetRecordingId ? this.runtime : null;
    const epoch = ownCapture?.epoch;
    const ownerId = ownCapture ? ownCapture.ownerId : signedInAccountId();
    try {
      if (ownCapture) {
        this.setState({ status: "finalizing", error: null, stopStage: null });
        await ownCapture.recorder.stop({ emitTail: false }).catch(() => {});
        await this.releaseRuntimeMedia();
      }
      await this.forCapture(targetRecordingId, ownerId, () =>
        discardRecordingCapture(targetRecordingId, undefined, { epoch }),
      );
    } catch (error: unknown) {
      // Discarded elsewhere already, which is what was asked for.
      if (!(error instanceof CaptureDiscardedError)) {
        if (isEndedForThisTab(error)) throw await this.ifEndedElsewhere(error, ownCapture);
        if (ownCapture) await this.settleAfterFailedStop(targetRecordingId, true, formatCaptureError(error));
        throw error;
      }
    }
    await this.disposeRuntime();
    // A check that began while the discard was in flight may still list it.
    this.captureActionTicket += 1;
    await this.segmentStore.clear(targetRecordingId).catch(() => {});
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

  /** Stored conflict IDs are not ownership; remote controls require a current offer. */
  private assertCaptureControlAllowed = (recordingId: RecordingId) => {
    if (this.runtime) {
      if (this.runtime.recordingId === recordingId) return;
      throw new Error("Use the controls for the recording captured in this tab.");
    }
    // Discovery already left out captures another tab of this browser holds.
    const offered = this.state.pausedRecording?.id === recordingId ||
      this.state.attentionRecordings.some((recording) => recording.id === recordingId);
    if (!offered) {
      throw new Error("Open the recording to recover it, or use the tab that is capturing it.");
    }
  };

  /**
   * Discards a paused recording this tab lost before it banked any audio.
   *
   * A document unload during capture is guarded: `handleGuardedExit` pauses the
   * recording so the audio already uploaded survives. That is right in the
   * middle of a meeting and wrong immediately after one starts. A `router.push`
   * degrades to a full-document navigation whenever Next.js finds the tab
   * running a different build from the server (any upgrade under an open tab
   * does it), and the app performs exactly such a push a second after `start()`
   * resolves. The guard then pauses a recording that is seconds old, and the
   * reloaded page opens the resume-or-discard modal over a meeting the user has
   * only just started.
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
    const context = readPausedCaptureContext();
    const paused = this.state.pausedRecording ?? this.state.attentionRecordings.find(
      (recording) => recording.id === context?.recordingId,
    );
    if (!paused || this.runtime) {
      return false;
    }

    if (
      !context ||
      context.recordingId !== paused.id ||
      context.lastSequence >= 0
    ) {
      return false;
    }

    // Audio recorded but never uploaded is still audio worth the user's decision.
    const queued = await this.segmentStore.list(paused.id).catch(() => []);
    if (queued.length > 0) {
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

  /**
   * The stored segments that continue what the server holds.
   *
   * Anything the server already has is removed from the store, since it was
   * uploaded. So is everything else that cannot be sent: audio past a gap,
   * which could never be finalized, and audio kept under an epoch other than
   * `epoch`, which was recorded before the capture was carried on elsewhere
   * and would land out of order or over the audio recorded since (issue
   * #570). That audio is kept to download instead (issue #605).
   */
  private loadQueuedSegments = (
    recordingId: RecordingId,
    serverLastSequence: number,
    epoch: number,
  ): Promise<StoredSegment[]> =>
    this.keptAudioTask(recordingId, async () => {
      const stored = await this.segmentStore.list(recordingId).catch((error: unknown) => {
        console.warn("[capture] could not read queued segments from the browser", error);
        return [] as StoredSegment[];
      });
      const ofThisCapture = (segment: StoredSegment) => storedSegmentEpoch(segment) === epoch;
      const usable = contiguousSegmentsAfter(stored.filter(ofThisCapture), serverLastSequence);
      const usableSequences = new Set(usable.map((segment) => segment.sequence));
      const leftOut: StoredSegment[] = [];
      for (const segment of stored) {
        if (usableSequences.has(segment.sequence)) {
          continue;
        }
        if (ofThisCapture(segment) && segment.sequence <= serverLastSequence) {
          void this.segmentStore.remove(recordingId, segment.sequence).catch(() => {});
        } else {
          leftOut.push(segment);
        }
      }
      const paused = this.state.pausedRecording;
      const kept = await this.keepLeftOutAudio(recordingId, leftOut, {
        recordingName: paused?.id === recordingId ? paused.name : undefined,
      });
      if (kept === 0) {
        this.reportLeftOutAudio(leftOut.filter((segment) => !ofThisCapture(segment)));
      }
      return usable;
    });

  /**
   * Runs `task` once any earlier one for the same recording's kept audio has
   * finished, so audio is set aside once and a resume reads the store only
   * after it has been. A check for finished captures passes over a recording
   * with a task under way.
   */
  private keptAudioTask = <T>(recordingId: RecordingId, task: () => Promise<T>): Promise<T> => {
    const previous = this.settingAside.get(recordingId) ?? Promise.resolve();
    const run = previous.catch(() => {}).then(task);
    this.settingAside.set(recordingId, run);
    const release = () => {
      if (this.settingAside.get(recordingId) === run) {
        this.settingAside.delete(recordingId);
      }
    };
    run.then(release, release);
    return run;
  };

  /**
   * Moves kept audio that can never join its recording out of the capture
   * store, keeping it as one FLAC file for its owner to download (issue #605).
   *
   * The segments leave the store whatever happens, as they did before there
   * was anything to download: left there, they would be sent with a later
   * capture of the recording or collide with audio it records. Returns the
   * seconds kept, 0 when none could be: the segments could not be read or
   * joined, the browser refused the write, or no account is known to own them.
   */
  private keepLeftOutAudio = async (
    recordingId: RecordingId,
    segments: StoredSegment[],
    details: { ownerId?: number; recordingName?: string },
  ): Promise<number> => {
    if (segments.length === 0) {
      return 0;
    }
    let kept = 0;
    try {
      const ownerId =
        details.ownerId ??
        segments.find((segment) => segment.ownerId !== undefined)?.ownerId ??
        signedInAccountId();
      const joined =
        ownerId === undefined
          ? null
          : joinFlacSegments(
              await Promise.all(
                segments.map(async (segment) => new Uint8Array(await segment.blob.arrayBuffer())),
              ),
            );
      if (ownerId !== undefined && joined) {
        const keptAt = Date.now();
        const seconds = joined.frames / joined.sampleRate;
        await this.leftOutAudio.put({
          id: `${recordingId}-${segments[0].sequence}-${keptAt}`,
          recordingId,
          ...(details.recordingName ? { recordingName: details.recordingName } : {}),
          ownerId,
          blob: new Blob([joined.bytes], { type: FLAC_MIME_TYPE }),
          seconds,
          keptAt,
        });
        kept = seconds;
      }
    } catch (error: unknown) {
      console.warn("[capture] could not keep audio left out of a recording to download", error);
    }
    await Promise.all(
      segments.map((segment) =>
        this.segmentStore.remove(recordingId, segment.sequence).catch(() => {}),
      ),
    );
    if (kept > 0) {
      console.info("[capture] kept audio left out of a recording to download", {
        recordingId,
        seconds: kept,
      });
      void this.offerLeftOutAudio();
    }
    return kept;
  };

  /** Offers the signed-in account the audio left out of its recordings. */
  private offerLeftOutAudio = async () => {
    try {
      await offerLeftOutAudio({
        store: this.leftOutAudio,
        closed: this.closedLeftOutOffers,
        signedInAs: async () =>
          signedInAccountId() ?? (await getUserMe().catch(() => null))?.id,
      });
    } catch (error: unknown) {
      console.warn("[capture] could not offer audio left out of a recording", error);
    }
  };

  /**
   * Keeps to download the audio a tab had not uploaded when its capture
   * continued or was stopped elsewhere (issue #605). Audio of a recording
   * discarded elsewhere is not offered: the check for finished captures
   * removes it, as the discard asked.
   */
  private setAsideStoodDownAudio = (runtime: ActiveRuntime | null, error: unknown) => {
    if (!runtime || error instanceof CaptureDiscardedError) {
      return Promise.resolve(0);
    }
    const { recordingId, epoch, ownerId, uploader } = runtime;
    return this.keptAudioTask(recordingId, async () => {
      await uploader.settled();
      const stored = await this.segmentStore.list(recordingId);
      const leftOut = stored.filter((segment) => storedSegmentEpoch(segment) === epoch);
      if (leftOut.length === 0) {
        return 0;
      }
      const recordingName = await getRecording(recordingId).then(
        (recording) => recording?.name,
        () => undefined,
      );
      return this.keepLeftOutAudio(recordingId, leftOut, { ownerId, recordingName });
    });
  };

  /**
   * Settles the audio this browser kept for captures that have finished
   * elsewhere (issues #589, #605), then offers what was left out.
   *
   * Kept audio is sent only when this browser resumes or stops its capture, so
   * audio kept for one stopped, discarded or taken over and finished on another
   * device would otherwise stay for good, and none of it can join the
   * recording. A capture the server still lists as unfinished keeps its audio,
   * whoever holds it. Any other is looked up first. Audio of a recording the
   * server reports finished is kept to download. Audio of one it says does not
   * exist is removed, but only while this page is signed in as the account
   * that kept the audio: another account gets the same 404 for a recording it
   * does not own.
   */
  private settleAudioKeptForFinishedCaptures = async (unfinished: Recording[]) => {
    if (this.checkingKeptAudio) {
      return;
    }
    this.checkingKeptAudio = true;
    try {
      const listed = new Set(unfinished.map((recording) => recording.id));
      for (const recordingId of await this.segmentStore.recordingIds()) {
        if (
          listed.has(recordingId) ||
          this.runtime?.recordingId === recordingId ||
          this.keptAudioUndecided.has(recordingId) ||
          this.settingAside.has(recordingId)
        ) {
          continue;
        }
        const verdict = await this.judgeKeptAudio(recordingId);
        if (verdict === "undecided") {
          this.keptAudioUndecided.add(recordingId);
        } else if (verdict === "gone") {
          console.info("[capture] cleared audio kept for a recording discarded elsewhere", recordingId);
          await this.keptAudioTask(recordingId, () => this.segmentStore.clear(recordingId));
        } else if (verdict !== "unfinished") {
          await this.keptAudioTask(recordingId, async () =>
            this.keepLeftOutAudio(recordingId, await this.segmentStore.list(recordingId), {
              recordingName: verdict.name,
            }),
          );
        }
      }
    } catch (error: unknown) {
      console.warn("[capture] could not check the audio this browser kept", error);
    } finally {
      this.checkingKeptAudio = false;
    }
    await this.offerLeftOutAudio();
  };

  /**
   * What became of the recording audio was kept for: the recording, once it
   * is finished; "gone" once the server says it does not exist while this page
   * is signed in as the account the audio was kept under; "undecided" when a
   * 404 cannot say; and "unfinished" while it may still take the audio or the
   * server could not be asked.
   */
  private judgeKeptAudio = async (
    recordingId: RecordingId,
  ): Promise<Recording | "unfinished" | "gone" | "undecided"> => {
    try {
      const recording = await getRecording(recordingId);
      return isFinishedCapture(recording) ? recording : "unfinished";
    } catch (error: unknown) {
      if (!(error instanceof AxiosError) || error.response?.status !== 404) {
        return "unfinished";
      }
      const [kept] = await this.segmentStore.list(recordingId);
      if (kept?.ownerId === undefined) {
        return "undecided";
      }
      // Asked rather than read from the session, which a page that has only
      // just loaded may not have filled in yet.
      const signedInAs = await getUserMe().catch(() => null);
      if (!signedInAs) {
        return "unfinished";
      }
      return signedInAs.id === kept.ownerId ? "gone" : "undecided";
    }
  };

  /** Says how much audio was left out when none of it could be kept to download. */
  private reportLeftOutAudio(leftOut: StoredSegment[]) {
    if (leftOut.length === 0) {
      return;
    }
    const seconds = leftOut.reduce((total, segment) => total + segment.durationSeconds, 0);
    console.warn("[capture] left out audio kept from before the capture continued elsewhere", {
      segments: leftOut.length,
      seconds,
    });
    useNotificationStore.getState().addNotification({
      type: "warning",
      message:
        `About ${describeAudioLength(seconds)} of audio this browser kept for ` +
        "this recording was left out, because the recording continued in " +
        "another tab or on another device after it was recorded.",
    });
  }

  /**
   * Uploads what a previous page recorded but never sent, before a stop.
   *
   * This is the stop for a recording paused by a closed or reloaded tab: there
   * is no live uploader, only what the store kept.
   */
  private uploadStoredSegments = async (recordingId: RecordingId) => {
    const stored = await this.segmentStore.list(recordingId).catch(() => []);
    if (stored.length === 0) {
      return;
    }
    this.setStopStage("flushing-uploads");
    // Pausing an already paused recording changes nothing and reports the last
    // sequence the server holds, and its epoch. Only audio a tab of this
    // browser kept under that epoch continues the capture, and it is sent
    // under it, as that tab would have.
    const ownerId = signedInAccountId();
    const response = await retryAfterSignIn(() =>
      this.forCapture(recordingId, ownerId, () => pauseRecordingCapture(recordingId)),
    );
    const segments = await this.loadQueuedSegments(
      recordingId,
      response.last_sequence,
      response.capture_epoch,
    );
    if (segments.length === 0) {
      return;
    }
    const uploader = createSegmentUploader({
      recordingId,
      epoch: response.capture_epoch,
      ownerId,
      initialSequence: response.last_sequence + 1,
      store: this.segmentStore,
      checkCaptureGone: (error) => this.captureGoneError(recordingId, ownerId, error),
      onQueueChange: (uploadQueue) => {
        this.setState({ uploadQueue });
      },
    });
    uploader.restore(segments);
    try {
      await uploader.waitForIdle({ timeoutMs: UPLOAD_FLUSH_TIMEOUT_MS });
    } finally {
      uploader.dispose();
      this.setState({ uploadQueue: EMPTY_UPLOAD_QUEUE });
    }
  };

  private activateRuntime = async (options: {
    recordingId: RecordingId;
    epoch: number;
    /** First sequence the recorder numbers new audio with. */
    startSequence: number;
    /** First sequence the server is missing, when queued audio precedes new audio. */
    uploadFromSequence?: number;
    restoredSegments?: StoredSegment[];
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

    const ownerId = signedInAccountId();
    const uploader = createSegmentUploader({
      recordingId: options.recordingId,
      epoch: options.epoch,
      ownerId,
      initialSequence: options.uploadFromSequence ?? options.startSequence,
      store: this.segmentStore,
      checkCaptureGone: (error) => this.captureGoneError(options.recordingId, ownerId, error),
      onQueueChange: (uploadQueue) => {
        this.setState({ uploadQueue });
      },
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
      onCaptureEnded: async (error) => {
        const runtime = this.runtime;
        // A stop under way reports it itself, once its flush fails.
        if (runtime?.uploader === uploader && this.state.status !== "finalizing") {
          await this.captureEndedElsewhere(runtime, error);
        }
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

    if (options.restoredSegments?.length) {
      uploader.restore(options.restoredSegments);
    }

    const recorder = createBrowserRecorder({
      source: mixer.pcmSource,
      startSequence: options.startSequence,
      onChunk: ({ sequence, blob, durationSeconds }) => {
        uploader.enqueue(sequence, blob, durationSeconds);
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
      epoch: options.epoch,
      ownerId,
      sources: options.sources,
      captureReport: options.captureReport,
      mixer,
      recorder,
      uploader,
      waveform,
      displayTracksCleanup,
      releaseCaptureLock: holdCaptureLock(options.recordingId),
    };
    this.stopCaptureRecheck();
    this.setState({ runtimeActive: true });
    this.startElapsedTimer(options.elapsedSeconds ?? this.state.elapsedSeconds);
    this.lifecycle.updateRecordingId(options.recordingId);
    this.lifecycle.resetGuard();
  };

  private handleUploaderStalled = async (
    recordingId: RecordingId,
    error: Error,
  ) => {
    const runtime = this.runtime;
    const ownCapture = Boolean(runtime) && this.state.recordingId === recordingId;
    if (ownCapture && this.state.status === "paused") {
      // The queue sent ahead of an owed pause stalled, and the pause goes next
      // anyway. After an earlier stall the user has already been told; a
      // pause from the controls warns if the server cannot be reached.
      return;
    }

    this.setState({ error: error.message || formatCaptureError(error) });

    if (!runtime || !ownCapture) {
      return;
    }

    if (this.state.status === "finalizing") {
      // stop() owns the stalled uploader here: it surfaces the failure and
      // leaves the recording resumable, so do not pause underneath it.
      return;
    }

    // The stalled uploader keeps its queued segments; pause capture so no
    // further audio is recorded until the user resumes and uploads retry.
    // This tab is paused from here on whether or not the server can be told,
    // and keeps the capture, its media and its queue (issue #568).
    await runtime.recorder.pause();
    // A stop may have begun while the recorder paused.
    if (this.runtime !== runtime || this.getState().status === "finalizing") {
      return;
    }
    releasePausedMicrophone(runtime, this.state.settings.releaseMicrophoneOnPause);
    this.stopElapsedTimer();
    writePausedCaptureContext({
      recordingId,
      lastSequence: this.state.lastSequence,
      persistedAt: Date.now(),
    });
    this.setState({ status: "paused" });
    this.unconfirmedPause = owePause(recordingId);
    // Straight to the pause: the queue has only just failed to send. Not
    // awaited, so the uploader's failed send ends here whatever the request
    // does.
    void this.sendServerPause({ drainFirst: false });
  };

  /** Runs one attempt to send the unconfirmed pause, or joins the one under way. */
  private sendServerPause = (options: { drainFirst: boolean }): Promise<unknown> => {
    if (!this.serverPauseAttempt) {
      this.serverPauseAttempt = this.attemptServerPause(options.drainFirst).finally(() => {
        this.serverPauseAttempt = null;
        // A pause owed while this attempt served an earlier one, which a
        // failed stop can do, has not been sent yet.
        if (this.unconfirmedPause && !this.stopWatchingForServer) {
          void this.sendServerPause({ drainFirst: false });
        }
      });
    }
    return this.serverPauseAttempt;
  };

  /**
   * Tells the server about the pause this tab has already made.
   *
   * Resolves with the error when the server still has not received it, and with
   * null once it has or once no pause is owed. Never rejects.
   */
  private attemptServerPause = async (drainFirst: boolean): Promise<unknown> => {
    const pause = this.unconfirmedPause;
    const runtime = this.runtime;
    if (!pause) {
      return null;
    }
    if (!runtime || runtime.recordingId !== pause.recordingId) {
      this.forgetUnconfirmedPause();
      return null;
    }

    if (drainFirst) {
      // As with a pause from the controls, the audio this tab kept goes before
      // the pause, so a stop or resume from anywhere afterwards has all of it.
      runtime.uploader.recover();
      try {
        await runtime.uploader.waitForIdle();
      } catch {
        // Sending failed again. The pause below says whether the API answers.
      }
      if (this.unconfirmedPause !== pause) {
        return null;
      }
    }

    try {
      const response = await retryAfterSignIn(() =>
        this.forCapture(pause.recordingId, runtime.ownerId, () =>
          pauseRecordingCapture(pause.recordingId, { epoch: runtime.epoch }),
        ),
      );
      if (this.unconfirmedPause !== pause) {
        return null;
      }
      this.forgetUnconfirmedPause();
      const lastSequence = Math.max(response.last_sequence, this.state.lastSequence);
      writePausedCaptureContext({
        recordingId: response.recording_id,
        lastSequence,
        persistedAt: Date.now(),
      });
      this.setState({
        lastSequence,
        elapsedSeconds: Math.max(
          this.state.elapsedSeconds,
          sequenceToElapsedSeconds(response.last_sequence),
        ),
      });
      await this.refreshPausedRecording().catch(() => {});
      return null;

    } catch (pauseError: unknown) {
      if (this.unconfirmedPause !== pause) {
        return null;
      }
      if (isEndedForThisTab(pauseError)) {
        this.forgetUnconfirmedPause();
        await this.captureEndedElsewhere(runtime, pauseError);
        return pauseError;
      }
      if (answeredByServer(pauseError)) {
        // A refusal is the server's answer, and asking again gets the same one.
        console.warn("[capture] the server refused the pause", pause.recordingId, pauseError);
        this.forgetUnconfirmedPause();
      } else {
        pause.failedAt = Date.now();
        pause.failures += 1;
        // A retry held back for the attempt before this one would go early.
        this.cancelOwedPauseRetry();
        this.watchForServer();
      }
      return pauseError || new Error("The pause did not reach the server.");
    }
  };

  /**
   * Retries the unconfirmed pause once the API is seen to answer after the last
   * failed attempt, and once that attempt's delay has passed.
   */
  private watchForServer() {
    if (this.stopWatchingForServer) {
      return;
    }
    const unsubscribe = useConnectivityStore.subscribe((connectivity) => {
      const pause = this.unconfirmedPause;
      if (
        pause &&
        connectivity.lastReachableAt !== null &&
        connectivity.lastReachableAt > pause.failedAt
      ) {
        this.retryOwedPauseWhenDue(pause);
      }
    });
    this.stopWatchingForServer = () => {
      unsubscribe();
      this.cancelOwedPauseRetry();
    };
  }

  private retryOwedPauseWhenDue(pause: UnconfirmedPause) {
    const waitMs = pause.failedAt + owedPauseRetryDelayMs(pause.failures) - Date.now();
    if (waitMs <= 0) {
      this.cancelOwedPauseRetry();
      void this.sendServerPause({ drainFirst: true });
      return;
    }
    if (this.owedPauseRetryTimerId) {
      return;
    }
    this.owedPauseRetryTimerId = setTimeout(() => {
      this.owedPauseRetryTimerId = null;
      if (this.unconfirmedPause === pause) {
        void this.sendServerPause({ drainFirst: true });
      }
    }, waitMs);
  }

  private cancelOwedPauseRetry() {
    if (this.owedPauseRetryTimerId) {
      clearTimeout(this.owedPauseRetryTimerId);
      this.owedPauseRetryTimerId = null;
    }
  }

  private forgetUnconfirmedPause() {
    this.unconfirmedPause = null;
    this.stopWatchingForServer?.();
    this.stopWatchingForServer = null;
  }

  /** Sends a pause the server has not received, before a resume. */
  private confirmUnconfirmedPause = async () => {
    await this.serverPauseAttempt;
    // Each attempt either settles the pause or reports why it could not.
    while (this.unconfirmedPause) {
      const error = await this.sendServerPause({ drainFirst: false });
      if (error) {
        throw error;
      }
    }
  };

  /** Whether closing the tab still has to tell the server about a pause. */
  private owesServerPause = () => Boolean(this.unconfirmedPause && this.runtime);

  /**
   * Stops this tab capturing once the server says the capture was resumed in
   * another tab or on another device (issue #570), or stopped (issue #590).
   *
   * Nothing this tab holds can join the recording any more, so its media, Web
   * Lock and uploader go, and it reads idle. The audio it had not uploaded
   * moves from the capture store to the audio kept for its owner to download
   * (issue #605).
   */
  private captureEndedElsewhere = async (runtime: ActiveRuntime | null, error: unknown) => {
    if (runtime && this.runtime !== runtime) {
      return;
    }
    const queuedSeconds = runtime?.uploader.pendingAudioSeconds() ?? 0;
    this.captureActionTicket += 1;
    if (runtime) {
      console.warn(
        isCaptureClosedError(error)
          ? "[capture] the recording was stopped elsewhere"
          : "[capture] the recording continued elsewhere",
        runtime.recordingId,
      );
      await runtime.recorder.stop({ emitTail: false }).catch(() => {});
      if (this.runtime !== runtime) {
        return;
      }
      await this.disposeRuntime();
    }
    clearPausedCaptureContext();
    this.resetCoverageTracking();
    this.setState({
      status: "idle",
      error: endedElsewhereMessage(error, queuedSeconds),
      lastSequence: -1,
      elapsedSeconds: 0,
      recordingId: null,
      pausedRecording: null,
      runtimeActive: false,
      levels: DEFAULT_CAPTURE_LEVELS,
      finalizeRetry: null,
      stopStage: null,
      coverageWarning: null,
    });
    this.lifecycle.updateRecordingId(null);
    // Neither is awaited: a user action rejects straight after this, and its
    // caller skips its own toast only while the error above is still set. The
    // check that the refresh starts passes over audio still being set aside.
    void this.setAsideStoodDownAudio(runtime, error).catch((setAsideError: unknown) => {
      console.warn("[capture] could not set aside the audio this tab had not uploaded", setAsideError);
    });
    void this.refreshPausedRecording().catch(() => {});
  };

  /**
   * A CaptureDiscardedError when a 404 for `recordingId` means it is gone,
   * and null otherwise (issue #602).
   *
   * The server answers 404 too for a recording another account owns, and tabs
   * share the session cookie, so after a sign-in as someone else in another
   * tab this tab's requests answer 404 while its capture still exists. The
   * recording is gone only when `/users/me` names `ownerId`, the account the
   * capture belongs to, and the recording still answers 404 after that: the
   * two went out apart, and the owner may have signed back in between. Under
   * another account, a page that holds sign-in hears it, so its dialog opens
   * and the request waits for the owner, as after an ended session.
   */
  private captureGoneError = async (
    recordingId: RecordingId,
    ownerId: number | undefined,
    error: unknown,
  ): Promise<CaptureDiscardedError | null> => {
    if (getErrorStatus(error) !== 404 || ownerId === undefined) {
      return null;
    }
    const signedInAs = await getUserMe().catch(() => null);
    if (!signedInAs) {
      return null;
    }
    if (signedInAs.id !== ownerId) {
      if (useSessionStore.getState().heldInPlace) {
        noteSignedIn(signedInAs);
      }
      return null;
    }
    const stillMissing = await getRecording(recordingId).then(
      () => false,
      (lookupError: unknown) => getErrorStatus(lookupError) === 404,
    );
    return stillMissing ? new CaptureDiscardedError() : null;
  };

  /**
   * Runs a request for a capture, failing it with a CaptureDiscardedError
   * once a 404 is confirmed to mean the recording is gone. Inside
   * `retryAfterSignIn`, a 404 under another account then waits for the owner.
   */
  private forCapture = async <T>(
    recordingId: RecordingId,
    ownerId: number | undefined,
    request: () => Promise<T>,
  ): Promise<T> => {
    try {
      return await request();
    } catch (error: unknown) {
      throw (await this.captureGoneError(recordingId, ownerId, error)) ?? error;
    }
  };

  /**
   * The error a user action rejects with. When the server refused it because
   * the capture ended for this tab elsewhere, this tab stands down first.
   */
  private ifEndedElsewhere = async (
    error: unknown,
    runtime: ActiveRuntime | null,
  ): Promise<unknown> => {
    if (!isEndedForThisTab(error)) {
      return error;
    }
    const message = endedElsewhereMessage(error, runtime?.uploader.pendingAudioSeconds() ?? 0);
    await this.captureEndedElsewhere(runtime, error);
    return new Error(this.state.error ?? message);
  };

  /**
   * Asks the backend how much audio it holds, and re-judges coverage.
   *
   * The server's figure is the one that counts: it is what was acknowledged
   * and decoded, and a segment the browser counted as sent but the server
   * quarantined as corrupt is not audio the meeting has.
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
   * diagnosing it. Blaming tab suspension by default sends people to check a
   * Chrome setting during an outage that is entirely server-side.
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

  /** Recorded audio the browser is still holding, measured from the queue. */
  private queuedAudioSeconds = (): number =>
    this.runtime?.uploader.pendingAudioSeconds() ?? 0;

  /**
   * Compares the audio the server holds against wall-clock recording time.
   */
  private evaluateCoverage = () => {
    if (this.state.status !== "recording") {
      return;
    }

    const capturedSeconds = this.capturedAudioSeconds;
    // Nothing to compare against until the first segment has been transcoded.
    // Silence beats guessing.
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

  private finalizeRecordingWhenReady = async (
    recordingId: RecordingId,
    clientElapsedSeconds: number | undefined,
    epoch: number | undefined,
    ownerId: number | undefined,
  ) => {
    const maxAttempts = FINALIZE_RETRY_DELAYS_MS.length;
    try {
      for (let attempt = 0; ; attempt += 1) {
        try {
          return await retryAfterSignIn(() =>
            this.forCapture(recordingId, ownerId, () =>
              finalizeRecordingCapture(recordingId, { clientElapsedSeconds, epoch }),
            ),
          );

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
    const epoch = this.runtime.epoch;
    // A pause made after an upload stall or a failed stop may not have
    // reached the server yet.
    const serverHoldsPause = this.state.status === "paused" && !this.owesServerPause();

    // Written before anything is awaited. This runs during an unload, so the
    // document can be torn down at the first yield and everything after it may
    // never execute. It is the only record of what this tab was capturing, and
    // discardEmptyInterruptedRecording needs its sequence count on the next
    // load to tell an interruption worth resuming from one worth clearing.
    writePausedCaptureContext({
      recordingId,
      lastSequence,
      persistedAt: Date.now(),
    });

    try {
      await this.runtime.recorder.stop({ emitTail: false });

      if (!serverHoldsPause) {
        const paused = request.useBeacon ? sendPauseBeacon(recordingId, epoch) : false;
        if (!paused) {
          const response = await pauseRecordingCapture(recordingId, { epoch });
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
    this.forgetUnconfirmedPause();
    this.stopElapsedTimer();
    await this.releaseRuntimeMedia();
    this.runtime = null;
    runtime.uploader.dispose();
    runtime.releaseCaptureLock();
    this.setState({
      levels: DEFAULT_CAPTURE_LEVELS,
      runtimeActive: false,
      uploadQueue: EMPTY_UPLOAD_QUEUE,
    });
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
    // A live capture, or a stop still sending audio, must outlive the session
    // ending: a refused request then asks for sign-in in place rather than
    // reloading to the sign-in page (issue #502).
    holdSignInInPlace(Boolean(this.runtime) || this.state.status === "finalizing");
    this.listeners.forEach((listener) => listener(this.state));
  }
}

export const createCaptureController = () => new CaptureController();
