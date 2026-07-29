export interface RecordedChunk {
  sequence: number;
  blob: Blob;
}

export interface RecorderStallInfo {
  /** Milliseconds since the recorder last emitted a segment. */
  sinceLastChunkMs: number;
  /** True when the stalled segment had to be abandoned without an onstop. */
  abandonedSegment: boolean;
}

export interface CreateBrowserRecorderOptions {
  stream: MediaStream;
  startSequence?: number;
  onChunk: (chunk: RecordedChunk) => void | Promise<void>;
  onError?: (error: Error) => void;
  onStall?: (info: RecorderStallInfo) => void;
  mediaRecorderCtor?: typeof MediaRecorder;
  timesliceMs?: number;
  stopTimeoutMs?: number;
  stallTimeoutMs?: number;
  now?: () => number;
}

const DEFAULT_MIME_TYPE = "audio/webm;codecs=opus";
const RECORDER_MIME_TYPE_CANDIDATES = [
  DEFAULT_MIME_TYPE,
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4;codecs=mp4a.40.2",
  "audio/mp4",
];
const DEFAULT_AUDIO_BITS_PER_SECOND = 160_000;

/**
 * How long to wait for MediaRecorder.onstop before abandoning the segment.
 *
 * stopActiveSegment used to await onstop unconditionally. Because every
 * pause/stop/roll goes through one serial operation queue, a MediaRecorder that
 * never fired onstop wedged the queue permanently and stop() could not reach
 * finalize (issue #166).
 */
const DEFAULT_STOP_TIMEOUT_MS = 5_000;

/** Multiple of the timeslice after which a silent recorder is treated as stalled. */
const STALL_TIMESLICE_MULTIPLE = 3;

const MIN_STALL_TIMEOUT_MS = 6_000;

const STALL_CHECK_INTERVAL_MS = 2_000;

const resolveRecorderMimeType = (mediaRecorderCtor: typeof MediaRecorder) => {
  return (
    RECORDER_MIME_TYPE_CANDIDATES.find((mimeType) =>
      mediaRecorderCtor.isTypeSupported(mimeType),
    ) ?? ""
  );
};

interface ActiveSegment {
  recorder: MediaRecorder;
  chunks: Blob[];
  timerId: ReturnType<typeof setTimeout> | null;
  stopping: boolean;
  stopPromise: Promise<Blob | null>;
  resolveStop: (blob: Blob | null) => void;
}

export class BrowserRecorder {
  private readonly stream: MediaStream;

  private readonly mediaRecorderCtor: typeof MediaRecorder;

  private readonly mimeType: string;

  private nextSequence: number;

  private onChunk: CreateBrowserRecorderOptions["onChunk"];

  private onError?: CreateBrowserRecorderOptions["onError"];

  private onStall?: CreateBrowserRecorderOptions["onStall"];

  private timesliceMs: number;

  private readonly stopTimeoutMs: number;

  private readonly stallTimeoutMs?: number;

  private readonly now: () => number;

  private activeSegment: ActiveSegment | null = null;

  private stateValue: RecordingState = "inactive";

  private operationQueue = Promise.resolve();

  private stopPromise: Promise<void> | null = null;

  private lastChunkAt = 0;

  private stallTimerId: ReturnType<typeof setInterval> | null = null;

  constructor(options: CreateBrowserRecorderOptions) {
    this.mediaRecorderCtor = options.mediaRecorderCtor ?? MediaRecorder;
    this.mimeType = resolveRecorderMimeType(this.mediaRecorderCtor);
    this.stream = options.stream;
    this.nextSequence = options.startSequence ?? 0;
    this.onChunk = options.onChunk;
    this.onError = options.onError;
    this.onStall = options.onStall;
    this.timesliceMs = options.timesliceMs ?? 2_000;
    this.stopTimeoutMs = options.stopTimeoutMs ?? DEFAULT_STOP_TIMEOUT_MS;
    this.stallTimeoutMs = options.stallTimeoutMs;
    this.now = options.now ?? (() => Date.now());
  }

  get state() {
    return this.stateValue;
  }

  /** Timestamp of the most recent emitted segment, for coverage tracking. */
  get lastChunkEmittedAt() {
    return this.lastChunkAt;
  }

  private get resolvedStallTimeoutMs() {
    return (
      this.stallTimeoutMs ??
      Math.max(this.timesliceMs * STALL_TIMESLICE_MULTIPLE, MIN_STALL_TIMEOUT_MS)
    );
  }

  start(timesliceMs = 2_000) {
    if (this.stateValue === "inactive") {
      this.timesliceMs = timesliceMs;
      this.stateValue = "recording";
      this.lastChunkAt = this.now();
      this.beginSegment();
      this.startStallWatchdog();
    }
  }

  async requestData() {
    const recorder = this.activeSegment?.recorder;
    if (!recorder || recorder.state !== "recording") {
      return;
    }

    recorder.requestData();
  }

  async pause() {
    if (this.stateValue !== "recording") {
      return;
    }

    this.stopStallWatchdog();
    await this.enqueueOperation(async () => {
      if (this.stateValue !== "recording") {
        return;
      }

      this.stateValue = "paused";
      const blob = await this.stopActiveSegment();
      this.emitChunk(blob);
    });
  }

  resume() {
    if (this.stateValue === "paused") {
      void this.enqueueOperation(async () => {
        if (this.stateValue !== "paused") {
          return;
        }

        this.stateValue = "recording";
        // Reset the stall marker: the paused interval is not a stall, and a
        // stale marker would trip the watchdog on the first tick after resume.
        this.lastChunkAt = this.now();
        this.beginSegment();
        this.startStallWatchdog();
      });
    }
  }

  stop(options: { emitTail?: boolean } = {}) {
    if (this.stateValue === "inactive" && !this.activeSegment) {
      return Promise.resolve();
    }

    if (!this.stopPromise) {
      const emitTail = options.emitTail !== false;
      this.stopStallWatchdog();
      this.stopPromise = this.enqueueOperation(async () => {
        this.stateValue = "inactive";
        const blob = await this.stopActiveSegment();
        if (emitTail) {
          this.emitChunk(blob);
        }
      }).finally(() => {
        this.stopPromise = null;
      });
    }

    return this.stopPromise;
  }

  private beginSegment() {
    if (this.stateValue !== "recording" || this.activeSegment) {
      return;
    }

    const recorder = new this.mediaRecorderCtor(this.stream, {
      mimeType: this.mimeType || undefined,
      audioBitsPerSecond: DEFAULT_AUDIO_BITS_PER_SECOND,
    });

    const chunks: Blob[] = [];
    let resolveStop: (blob: Blob | null) => void = () => {};
    const segment: ActiveSegment = {
      recorder,
      chunks,
      timerId: null,
      stopping: false,
      stopPromise: new Promise<Blob | null>((resolve) => {
        resolveStop = resolve;
      }),
      resolveStop,
    };

    recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data.size > 0) {
        chunks.push(event.data);
      }
    };

    recorder.onerror = () => {
      this.onError?.(new Error("The browser recorder reported an unexpected error."));
    };

    recorder.onstop = () => {
      if (segment.timerId) {
        clearTimeout(segment.timerId);
        segment.timerId = null;
      }

      const type = recorder.mimeType || this.mimeType || DEFAULT_MIME_TYPE;
      segment.resolveStop(
        chunks.length > 0 ? new Blob(chunks, { type }) : null,
      );
    };

    this.activeSegment = segment;
    recorder.start();
    segment.timerId = setTimeout(() => {
      void this.rollSegment();
    }, this.timesliceMs);
  }

  private async rollSegment() {
    await this.enqueueOperation(async () => {
      if (this.stateValue !== "recording") {
        return;
      }

      const blob = await this.stopActiveSegment();
      this.emitChunk(blob);

      if (this.stateValue === "recording") {
        this.beginSegment();
      }
    });
  }

  private async stopActiveSegment() {
    const segment = this.activeSegment;
    if (!segment) {
      return null;
    }

    this.activeSegment = null;
    if (segment.timerId) {
      clearTimeout(segment.timerId);
      segment.timerId = null;
    }

    if (!segment.stopping) {
      segment.stopping = true;
      if (segment.recorder.state !== "inactive") {
        segment.recorder.stop();
      } else {
        segment.resolveStop(null);
      }
    }

    // Never await onstop unconditionally: a recorder that never fires it would
    // wedge the shared operation queue and every later pause/stop with it. On
    // expiry, keep whatever the segment did buffer rather than losing it.
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    const abandoned = Symbol("abandoned");
    const timeout = new Promise<typeof abandoned>((resolve) => {
      timeoutId = setTimeout(() => resolve(abandoned), this.stopTimeoutMs);
    });

    try {
      const result = await Promise.race([segment.stopPromise, timeout]);
      if (result !== abandoned) {
        return result;
      }
    } finally {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    }

    this.onStall?.({
      sinceLastChunkMs: this.now() - this.lastChunkAt,
      abandonedSegment: true,
    });

    const type = segment.recorder.mimeType || this.mimeType || DEFAULT_MIME_TYPE;
    return segment.chunks.length > 0
      ? new Blob(segment.chunks, { type })
      : null;
  }

  private startStallWatchdog() {
    this.stopStallWatchdog();
    this.stallTimerId = setInterval(() => {
      this.checkForStall();
    }, STALL_CHECK_INTERVAL_MS);
  }

  private stopStallWatchdog() {
    if (this.stallTimerId) {
      clearInterval(this.stallTimerId);
      this.stallTimerId = null;
    }
  }

  /**
   * Restarts the segment chain when it has gone quiet while still recording.
   *
   * A suspended tab (OS sleep, browser tab freezing) stops both the roll timer
   * and the MediaRecorder's audio feed, and neither surfaces an error: state
   * stays "recording" with an active segment while nothing reaches disk. Rolling
   * the segment restarts the chain and makes the gap visible (issue #166).
   */
  private checkForStall() {
    if (this.stateValue !== "recording") {
      return;
    }

    const sinceLastChunkMs = this.now() - this.lastChunkAt;
    if (sinceLastChunkMs < this.resolvedStallTimeoutMs) {
      return;
    }

    // Move the marker first so a slow restart does not retrigger every tick.
    this.lastChunkAt = this.now();
    this.onStall?.({ sinceLastChunkMs, abandonedSegment: false });
    void this.rollSegment();
  }

  private emitChunk(blob: Blob | null) {
    if (!blob || blob.size <= 0) {
      return;
    }

    const sequence = this.nextSequence;
    this.nextSequence += 1;
    this.lastChunkAt = this.now();
    void Promise.resolve(this.onChunk({ sequence, blob })).catch((error) => {
      this.onError?.(
        error instanceof Error
          ? error
          : new Error("The browser recorder failed to queue a segment."),
      );
    });
  }

  private enqueueOperation<T>(operation: () => Promise<T>) {
    const nextOperation = this.operationQueue.then(operation, operation);
    this.operationQueue = nextOperation.then(
      () => undefined,
      () => undefined,
    );
    return nextOperation;
  }
}

export const createBrowserRecorder = (options: CreateBrowserRecorderOptions) =>
  new BrowserRecorder(options);
