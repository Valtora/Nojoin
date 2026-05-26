import type { RecordingId } from "@/types";
import { uploadRecordingSegment } from "@/lib/api";

const DEFAULT_RETRY_DELAYS_MS = [500, 1_000, 2_000, 4_000, 8_000];

export interface SegmentUploaderOptions {
  recordingId: RecordingId;
  initialSequence?: number;
  uploadSegment?: typeof uploadRecordingSegment;
  onUploaded?: (sequence: number) => void;
  onFatal?: (error: Error) => void | Promise<void>;
  retryDelaysMs?: number[];
  wait?: (ms: number) => Promise<void>;
}

export class SegmentUploader {
  private readonly recordingId: RecordingId;

  private readonly uploadSegmentFn: typeof uploadRecordingSegment;

  private readonly onUploaded?: (sequence: number) => void;

  private readonly onFatal?: (error: Error) => void | Promise<void>;

  private readonly retryDelaysMs: number[];

  private readonly wait: (ms: number) => Promise<void>;

  private readonly pending = new Map<number, Blob>();

  private nextExpectedSequence: number;

  private drainPromise: Promise<void> | null = null;

  private drainScheduled = false;

  private fatalError: Error | null = null;

  private closed = false;

  constructor(options: SegmentUploaderOptions) {
    this.recordingId = options.recordingId;
    this.uploadSegmentFn = options.uploadSegment ?? uploadRecordingSegment;
    this.onUploaded = options.onUploaded;
    this.onFatal = options.onFatal;
    this.retryDelaysMs = options.retryDelaysMs ?? DEFAULT_RETRY_DELAYS_MS;
    this.wait = options.wait ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    this.nextExpectedSequence = options.initialSequence ?? 0;
  }

  enqueue(sequence: number, blob: Blob) {
    if (this.closed || this.fatalError) {
      return;
    }

    this.pending.set(sequence, blob);
    this.scheduleDrain();
  }

  async waitForIdle() {
    while (true) {
      if (this.fatalError) {
        throw this.fatalError;
      }

      if (!this.drainPromise) {
        if (this.pending.size === 0 || this.closed) {
          return;
        }

        if (!this.pending.has(this.nextExpectedSequence)) {
          await Promise.resolve();
          continue;
        }

        await this.drain().catch(() => {});
        continue;
      }

      await this.drainPromise.catch(() => {});
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
        while (!this.closed && !this.fatalError) {
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
        if (!this.closed && !this.fatalError && this.pending.has(this.nextExpectedSequence)) {
          this.scheduleDrain();
          return;
        }

      }
    })();

    return this.drainPromise;
  }

  private scheduleDrain() {
    if (this.drainScheduled || this.drainPromise || this.closed || this.fatalError) {
      return;
    }

    this.drainScheduled = true;
    queueMicrotask(() => {
      this.drainScheduled = false;
      if (
        this.drainPromise ||
        this.closed ||
        this.fatalError ||
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
      } catch (error) {
        lastError =
          error instanceof Error
            ? error
            : new Error("The browser uploader failed to send a recording segment.");

        if (attempt === this.retryDelaysMs.length) {
          this.fatalError = lastError;
          this.closed = true;
          await this.onFatal?.(lastError);
          throw lastError;
        }

        await this.wait(this.retryDelaysMs[attempt]);
      }
    }
  }
}

export const createSegmentUploader = (options: SegmentUploaderOptions) =>
  new SegmentUploader(options);