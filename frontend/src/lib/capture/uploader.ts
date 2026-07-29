import type { RecordingId } from "@/types";
import { uploadRecordingSegment } from "@/lib/api";

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
  initialSequence?: number;
  uploadSegment?: typeof uploadRecordingSegment;
  onUploaded?: (sequence: number) => void;
  onStalled?: (error: Error) => void | Promise<void>;
  retryDelaysMs?: number[];
  wait?: (ms: number) => Promise<void>;
  now?: () => number;
}

export class SegmentUploader {
  private readonly recordingId: RecordingId;

  private readonly uploadSegmentFn: typeof uploadRecordingSegment;

  private readonly onUploaded?: (sequence: number) => void;

  private readonly onStalled?: (error: Error) => void | Promise<void>;

  private readonly retryDelaysMs: number[];

  private readonly wait: (ms: number) => Promise<void>;

  private readonly now: () => number;

  private readonly pending = new Map<number, Blob>();

  private nextExpectedSequence: number;

  private drainPromise: Promise<void> | null = null;

  private drainScheduled = false;

  private stalledError: Error | null = null;

  private closed = false;

  constructor(options: SegmentUploaderOptions) {
    this.recordingId = options.recordingId;
    this.uploadSegmentFn = options.uploadSegment ?? uploadRecordingSegment;
    this.onUploaded = options.onUploaded;
    this.onStalled = options.onStalled;
    this.retryDelaysMs = options.retryDelaysMs ?? DEFAULT_RETRY_DELAYS_MS;
    this.wait = options.wait ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    this.now = options.now ?? (() => Date.now());
    this.nextExpectedSequence = options.initialSequence ?? 0;
  }

  enqueue(sequence: number, blob: Blob) {
    if (this.closed) {
      return;
    }

    // Segments are kept while the uploader is stalled so a later recover()
    // can retry them instead of dropping recorded audio.
    this.pending.set(sequence, blob);
    this.scheduleDrain();
  }

  /**
   * Clears a stalled state and retries the queued segments. Returns false
   * only when the uploader has been disposed and cannot upload again.
   */
  recover() {
    if (this.closed) {
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
   * wait here meant a stalled queue silently prevented finalize from ever being
   * called (issue #166). On expiry the caller gets an UploaderTimeoutError and
   * can decide, rather than hanging.
   */
  async waitForIdle(options: { timeoutMs?: number } = {}) {
    const timeoutMs = options.timeoutMs ?? DEFAULT_IDLE_TIMEOUT_MS;
    const deadline = this.now() + timeoutMs;

    while (true) {
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

          // A real macrotask yield. Spinning on a microtask here starved the
          // event loop instead of yielding, locking up the tab rather than
          // merely waiting.
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
        while (!this.closed && !this.stalledError) {
          const nextBlob = this.pending.get(this.nextExpectedSequence);
          if (!nextBlob) {
            break;
          }

          this.pending.delete(this.nextExpectedSequence);
          await this.uploadWithRetry(this.nextExpectedSequence, nextBlob);
          this.onUploaded?.(this.nextExpectedSequence);
          this.nextExpectedSequence += 1;
        }
      } finally {
        this.drainPromise = null;
        if (!this.closed && !this.stalledError && this.pending.has(this.nextExpectedSequence)) {
          this.scheduleDrain();
          return;
        }

      }
    })();

    return this.drainPromise;
  }

  private scheduleDrain() {
    if (this.drainScheduled || this.drainPromise || this.closed || this.stalledError) {
      return;
    }

    this.drainScheduled = true;
    queueMicrotask(() => {
      this.drainScheduled = false;
      if (
        this.drainPromise ||
        this.closed ||
        this.stalledError ||
        !this.pending.has(this.nextExpectedSequence)
      ) {
        return;
      }

      void this.drain().catch(() => {});
    });
  }

  private async uploadWithRetry(sequence: number, blob: Blob) {
    let lastError: Error | null = null;

    for (let attempt = 0; attempt <= this.retryDelaysMs.length; attempt += 1) {
      try {
        await this.uploadSegmentFn(this.recordingId, sequence, blob);
        return;

            } catch (error: unknown) {
        lastError =
          error instanceof Error
            ? error
            : new Error("The browser uploader failed to send a recording segment.");

        if (attempt === this.retryDelaysMs.length) {
          // Put the segment back so recover() can retry it later; the
          // uploader stalls rather than permanently dropping audio.
          this.pending.set(sequence, blob);
          this.stalledError = lastError;
          await this.onStalled?.(lastError);
          throw lastError;
        }

        await this.wait(this.retryDelaysMs[attempt]);
      }
    }
  }
}

export const createSegmentUploader = (options: SegmentUploaderOptions) =>
  new SegmentUploader(options);
