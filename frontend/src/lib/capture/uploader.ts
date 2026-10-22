import type { RecordingId } from "@/types";
import { isCaptureEndedError, uploadRecordingSegment } from "@/lib/api";
import { waitForSignInAfter } from "@/lib/session";

import { storedSegmentEpoch, type SegmentStore, type StoredSegment } from "./segmentStore";
import type { UploadQueueSize } from "./shared";

const DEFAULT_RETRY_DELAYS_MS = [500, 1_000, 2_000, 4_000, 8_000];

/**
 * How long a queued-sequence gap is tolerated before waitForIdle gives up. The
 * recorder emits sequences in order, so a gap means a segment was dropped and
 * waiting for it can never succeed.
 */
const DEFAULT_IDLE_TIMEOUT_MS = 60_000;

const GAP_POLL_INTERVAL_MS = 50;

export class UploaderTimeoutError extends Error {
  readonly pendingSequences: number[];

  readonly expectedSequence: number;

  constructor(expectedSequence: number, pendingSequences: number[]) {
    super(
      `Timed out waiting for recording segment ${expectedSequence} to upload.`,
    );
    this.name = "UploaderTimeoutError";
    this.expectedSequence = expectedSequence;
    this.pendingSequences = pendingSequences;
  }
}

export interface SegmentUploaderOptions {
  recordingId: RecordingId;
  /**
   * The capture epoch this tab holds. Sent with every segment, so the server
   * refuses them once the capture is resumed elsewhere (issue #570).
   */
  epoch: number;
  /** The account the capture belongs to, kept with each stored segment. */
  ownerId?: number;
  initialSequence?: number;
  uploadSegment?: typeof uploadRecordingSegment;
  onUploaded?: (sequence: number) => void;
  onStalled?: (error: Error) => void | Promise<void>;
  /**
   * Called once when the server refuses a segment because the capture has
   * ended for this tab: resumed in another tab or on another device, or
   * stopped. Nothing is uploaded after it, and the queue stays in the store.
   */
  onCaptureEnded?: (error: Error) => void | Promise<void>;
  /**
   * Looks into a failed upload that is not plainly the capture ending, and
   * resolves an error when it turns out to be one, as a 404 for a recording
   * discarded elsewhere does once the account is confirmed (issue #602). The
   * uploader then stops as for any other ending.
   */
  checkCaptureGone?: (error: unknown) => Promise<Error | null>;
  /** Called whenever the queue grows or shrinks. */
  onQueueChange?: (size: UploadQueueSize) => void;
  /** Where queued segments survive a closed tab. Omitted, the queue is memory only. */
  store?: SegmentStore;
  /**
   * Resolves true once a failed upload can be sent again because the user has
   * signed back in, or false when the failure is an ordinary one. Defaults to
   * the browser session's.
   */
  waitForSignIn?: (error: unknown) => Promise<boolean>;
  retryDelaysMs?: number[];
  wait?: (ms: number) => Promise<void>;
  now?: () => number;
}

interface QueuedSegment {
  blob: Blob;
  durationSeconds: number;
}

export class SegmentUploader {
  private readonly recordingId: RecordingId;

  private readonly epoch: number;

  private readonly ownerId?: number;

  private readonly uploadSegmentFn: typeof uploadRecordingSegment;

  private readonly onUploaded?: (sequence: number) => void;

  private readonly onStalled?: (error: Error) => void | Promise<void>;

  private readonly onCaptureEnded?: (error: Error) => void | Promise<void>;

  private readonly checkCaptureGone?: (error: unknown) => Promise<Error | null>;

  private readonly onQueueChange?: (size: UploadQueueSize) => void;

  private readonly store?: SegmentStore;

  /** Pending writes to the store, so a removal never overtakes its write. */
  private readonly persisting = new Map<number, Promise<void>>();

  private readonly retryDelaysMs: number[];

  private readonly waitForSignIn: (error: unknown) => Promise<boolean>;

  private readonly wait: (ms: number) => Promise<void>;

  private readonly now: () => number;

  private readonly pending = new Map<number, QueuedSegment>();

  private nextExpectedSequence: number;

  private drainPromise: Promise<void> | null = null;

  private drainScheduled = false;

  private stalledError: Error | null = null;

  /** Set once the capture has ended for this tab. Unlike a stall, it never clears. */
  private endedError: Error | null = null;

  private closed = false;

  constructor(options: SegmentUploaderOptions) {
    this.recordingId = options.recordingId;
    this.epoch = options.epoch;
    this.ownerId = options.ownerId;
    this.uploadSegmentFn = options.uploadSegment ?? uploadRecordingSegment;
    this.onUploaded = options.onUploaded;
    this.onStalled = options.onStalled;
    this.onCaptureEnded = options.onCaptureEnded;
    this.checkCaptureGone = options.checkCaptureGone;
    this.onQueueChange = options.onQueueChange;
    this.store = options.store;
    this.retryDelaysMs = options.retryDelaysMs ?? DEFAULT_RETRY_DELAYS_MS;
    this.waitForSignIn = options.waitForSignIn ?? waitForSignInAfter;
    this.wait = options.wait ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    this.now = options.now ?? (() => Date.now());
    this.nextExpectedSequence = options.initialSequence ?? 0;
  }

  /**
   * Segments recorded but not yet acknowledged by the server.
   *
   * This is audio the browser is holding, not audio that has been lost, and
   * the coverage check needs the difference: a server that stops answering for
   * two minutes leaves the queue two minutes long, and counting that as missing
   * tells the user their meeting is being lost while it is in fact sitting here
   * waiting to upload.
   */
  pendingSegmentCount(): number {
    return this.pending.size;
  }

  /** Exact length of the audio still queued. */
  pendingAudioSeconds(): number {
    let seconds = 0;
    this.pending.forEach((segment) => {
      seconds += segment.durationSeconds;
    });
    return seconds;
  }

  enqueue(sequence: number, blob: Blob, durationSeconds = 0) {
    if (this.closed) {
      return;
    }

    // Segments are kept while the uploader is stalled so a later recover()
    // can retry them instead of dropping recorded audio.
    this.pending.set(sequence, { blob, durationSeconds });
    this.persist(sequence, blob, durationSeconds);
    this.notifyQueueChange();
    this.scheduleDrain();
  }

  /**
   * Queues segments a previous page left in the store, ahead of new audio.
   *
   * Each is removed from the store once the server acknowledges it. One kept
   * under an earlier epoch, or without its owner, is stored again under this
   * uploader's, so a later page still finds it belongs to the capture if this
   * one closes before it is sent.
   */
  restore(segments: StoredSegment[]) {
    if (this.closed) {
      return;
    }
    for (const segment of segments) {
      if (segment.sequence < this.nextExpectedSequence) {
        continue;
      }
      this.pending.set(segment.sequence, {
        blob: segment.blob,
        durationSeconds: segment.durationSeconds,
      });
      if (
        storedSegmentEpoch(segment) !== this.epoch ||
        (segment.ownerId === undefined && this.ownerId !== undefined)
      ) {
        this.persist(segment.sequence, segment.blob, segment.durationSeconds);
      }
    }
    this.notifyQueueChange();
    this.scheduleDrain();
  }

  /**
   * Clears a stalled state and retries the queued segments. Returns false
   * when the uploader has been disposed, or the capture was resumed or
   * stopped elsewhere, and it cannot upload again.
   */
  recover() {
    if (this.closed || this.endedError) {
      return false;
    }

    if (this.stalledError) {
      this.stalledError = null;
      this.scheduleDrain();
    }
    return true;
  }

  /**
   * Resolves once every queued segment has uploaded.
   *
   * Bounded on purpose: stop() awaits this before finalizing, and an unbounded
   * wait would let a stalled queue prevent finalize from ever being called
   * (issue #166). On expiry the caller gets an UploaderTimeoutError and can
   * decide, rather than hanging.
   */
  async waitForIdle(options: { timeoutMs?: number } = {}) {
    const timeoutMs = options.timeoutMs ?? DEFAULT_IDLE_TIMEOUT_MS;
    const deadline = this.now() + timeoutMs;

    while (true) {
      if (this.endedError) {
        throw this.endedError;
      }
      if (this.stalledError) {
        throw this.stalledError;
      }

      if (!this.drainPromise) {
        if (this.pending.size === 0 || this.closed) {
          return;
        }

        if (!this.pending.has(this.nextExpectedSequence)) {
          if (this.now() >= deadline) {
            throw new UploaderTimeoutError(
              this.nextExpectedSequence,
              [...this.pending.keys()].sort((a, b) => a - b),
            );
          }

          // A real macrotask yield: spinning on a microtask starves the event
          // loop and locks up the tab.
          await this.wait(GAP_POLL_INTERVAL_MS);
          continue;
        }

        await this.drain().catch(() => {});
        continue;
      }

      await this.drainPromise.catch(() => {});

      if (this.now() >= deadline && !this.closed && this.pending.size > 0) {
        throw new UploaderTimeoutError(
          this.nextExpectedSequence,
          [...this.pending.keys()].sort((a, b) => a - b),
        );
      }
    }
  }

  dispose() {
    this.closed = true;
    this.pending.clear();
  }

  private drain() {
    if (this.drainPromise) {
      return this.drainPromise;
    }

    this.drainPromise = (async () => {
      try {
        while (!this.closed && !this.stalledError && !this.endedError) {
          const next = this.pending.get(this.nextExpectedSequence);
          if (!next) {
            break;
          }

          const sequence = this.nextExpectedSequence;
          await this.uploadWithRetry(sequence, next);
          this.pending.delete(sequence);
          this.forget(sequence);
          this.notifyQueueChange();
          this.onUploaded?.(sequence);
          this.nextExpectedSequence += 1;
        }
      } finally {
        this.drainPromise = null;
        if (
          !this.closed &&
          !this.stalledError &&
          !this.endedError &&
          this.pending.has(this.nextExpectedSequence)
        ) {
          this.scheduleDrain();
          return;
        }

      }
    })();

    return this.drainPromise;
  }

  private scheduleDrain() {
    if (
      this.drainScheduled ||
      this.drainPromise ||
      this.closed ||
      this.stalledError ||
      this.endedError
    ) {
      return;
    }

    this.drainScheduled = true;
    queueMicrotask(() => {
      this.drainScheduled = false;
      if (
        this.drainPromise ||
        this.closed ||
        this.stalledError ||
        this.endedError ||
        !this.pending.has(this.nextExpectedSequence)
      ) {
        return;
      }

      void this.drain().catch(() => {});
    });
  }

  /** Keeps a queued segment in the store under this uploader's epoch. */
  private persist(sequence: number, blob: Blob, durationSeconds: number) {
    if (!this.store) {
      return;
    }
    this.persisting.set(
      sequence,
      this.store
        .put({
          recordingId: this.recordingId,
          sequence,
          blob,
          durationSeconds,
          epoch: this.epoch,
          ...(this.ownerId === undefined ? {} : { ownerId: this.ownerId }),
        })
        .catch((error: unknown) => {
          console.warn("[capture] could not keep a queued segment in the browser", error);
        }),
    );
  }

  /** Drops an acknowledged segment from the store, after its write if that is still running. */
  private forget(sequence: number) {
    if (!this.store) {
      return;
    }
    const store = this.store;
    const written = this.persisting.get(sequence) ?? Promise.resolve();
    this.persisting.delete(sequence);
    void written
      .then(() => store.remove(this.recordingId, sequence))
      .catch((error: unknown) => {
        console.warn("[capture] could not clear an uploaded segment from the browser", error);
      });
  }

  private notifyQueueChange() {
    this.onQueueChange?.({
      segments: this.pending.size,
      seconds: this.pendingAudioSeconds(),
    });
  }

  private async uploadWithRetry(sequence: number, segment: QueuedSegment) {
    let attempt = 0;

    for (;;) {
      try {
        await this.uploadSegmentFn(this.recordingId, sequence, segment.blob, {
          epoch: this.epoch,
        });
        return;

      } catch (error: unknown) {
        // Another tab or device resumed, stopped or discarded the capture.
        // Retrying gets the same answer, and the segment can never join the
        // recording, so it stays in the store and nothing more is sent.
        const gone = isCaptureEndedError(error)
          ? error instanceof Error
            ? error
            : new Error("This recording ended elsewhere.")
          : await this.checkCaptureGone?.(error);
        if (gone) {
          this.endedError = gone;
          await this.onCaptureEnded?.(gone);
          throw gone;
        }

        // A failure while the session is unusable is not a failed attempt. The
        // segment, and every one recorded after it, waits for the user to sign
        // back in, however long that takes, rather than stalling the capture.
        if (await this.waitForSignIn(error)) {
          if (this.closed) {
            throw new Error("The browser uploader was closed while waiting for sign-in.");
          }
          continue;
        }

        const lastError =
          error instanceof Error
            ? error
            : new Error("The browser uploader failed to send a recording segment.");

        if (attempt === this.retryDelaysMs.length) {
          // The segment stays queued so recover() can retry it later; the
          // uploader stalls rather than permanently dropping audio.
          this.stalledError = lastError;
          await this.onStalled?.(lastError);
          throw lastError;
        }

        await this.wait(this.retryDelaysMs[attempt]);
        attempt += 1;
      }
    }
  }
}

export const createSegmentUploader = (options: SegmentUploaderOptions) =>
  new SegmentUploader(options);
