/**
 * The screen study's upload queue (#653): memory only, never browser storage.
 *
 * It holds at most 30 frames and sends one at a time. It starts a frame only
 * while no audio is waiting to upload, so frames queue behind segments; a
 * frame already sending when a segment arrives still finishes first. Full, it
 * drops the oldest frame. A failed upload stops it for good and says why, and
 * the study turns itself off; the recording is never touched. Disposing of it
 * aborts the frame in flight.
 */

export const FRAME_QUEUE_CAPACITY = 30;

export interface QueuedFrame {
  sequence: number;
  offsetMs: number;
  blob: Blob;
  /** The session's audio tick the frame was sampled on, from 0. */
  tick: number;
  /** The VideoFrame's timestamp, in microseconds. */
  frameTsUs: number;
  /** From the audio tick to drawing the frame, in milliseconds. */
  grabDelayMs: number;
}

export interface FrameQueueOptions<Result> {
  send: (frame: QueuedFrame, signal: AbortSignal) => Promise<Result>;
  /** Whether the capture has audio waiting to upload. */
  audioWaiting: () => boolean;
  onSent?: (result: Result, frame: QueuedFrame) => void;
  onFailed?: (error: unknown, frame: QueuedFrame) => void;
  /** A frame that will never be sent: dropped when full, or left at dispose. */
  onDropped?: (frame: QueuedFrame) => void;
  onChange?: (state: FrameQueueState) => void;
  capacity?: number;
}

export interface FrameQueueState {
  queued: number;
  dropped: number;
  sending: boolean;
  failed: boolean;
}

export class FrameQueue<Result = unknown> {
  private readonly frames: QueuedFrame[] = [];

  private readonly options: FrameQueueOptions<Result>;

  private readonly capacity: number;

  private inFlight: AbortController | null = null;

  private failed = false;

  private disposed = false;

  private dropped = 0;

  private idleWaiters: Array<() => void> = [];

  constructor(options: FrameQueueOptions<Result>) {
    this.options = options;
    this.capacity = options.capacity ?? FRAME_QUEUE_CAPACITY;
  }

  get state(): FrameQueueState {
    return {
      queued: this.frames.length,
      dropped: this.dropped,
      sending: this.inFlight !== null,
      failed: this.failed,
    };
  }

  enqueue(frame: QueuedFrame) {
    if (this.failed || this.disposed) {
      this.options.onDropped?.(frame);
      return;
    }
    this.frames.push(frame);
    while (this.frames.length > this.capacity) {
      const oldest = this.frames.shift() as QueuedFrame;
      this.dropped += 1;
      this.options.onDropped?.(oldest);
    }
    this.changed();
    this.pump();
  }

  /** The capture's audio queue changed: frames may go now. */
  audioQueueChanged() {
    this.pump();
  }

  /** Resolves once nothing is queued or sending, or after `timeoutMs`. */
  drain(timeoutMs: number): Promise<boolean> {
    this.pump();
    if (this.isIdle()) {
      return Promise.resolve(true);
    }
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.idleWaiters = this.idleWaiters.filter((waiter) => waiter !== done);
        resolve(false);
      }, timeoutMs);
      const done = () => {
        clearTimeout(timer);
        resolve(true);
      };
      this.idleWaiters.push(done);
    });
  }

  /** Drops what is queued, aborts the frame in flight and sends nothing more. */
  dispose() {
    if (this.disposed) {
      return;
    }
    this.disposed = true;
    const left = this.frames.splice(0);
    left.forEach((frame) => this.options.onDropped?.(frame));
    this.inFlight?.abort();
    this.changed();
    this.settleIdle();
  }

  private isIdle() {
    return this.frames.length === 0 && this.inFlight === null;
  }

  private pump() {
    if (this.inFlight || this.failed || this.disposed || this.frames.length === 0) {
      this.settleIdle();
      return;
    }
    if (this.options.audioWaiting()) {
      return;
    }
    const frame = this.frames.shift() as QueuedFrame;
    const controller = new AbortController();
    this.inFlight = controller;
    this.changed();
    void this.options
      .send(frame, controller.signal)
      .then(
        (result) => {
          if (!this.disposed) {
            this.options.onSent?.(result, frame);
          }
        },
        (error: unknown) => {
          this.failed = true;
          const left = this.frames.splice(0);
          if (this.disposed) {
            this.options.onDropped?.(frame);
            return;
          }
          this.options.onFailed?.(error, frame);
          left.forEach((item) => this.options.onDropped?.(item));
        },
      )
      .finally(() => {
        this.inFlight = null;
        this.changed();
        this.pump();
      });
  }

  private settleIdle() {
    if (!this.isIdle() || this.idleWaiters.length === 0) {
      return;
    }
    const waiters = this.idleWaiters;
    this.idleWaiters = [];
    waiters.forEach((waiter) => waiter());
  }

  private changed() {
    this.options.onChange?.(this.state);
  }
}
