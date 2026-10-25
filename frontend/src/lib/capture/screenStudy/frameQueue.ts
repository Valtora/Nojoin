/**
 * The screen study's upload queue (#653): memory only, never browser storage.
 *
 * It holds at most 30 frames and sends one at a time. It sends nothing while
 * any audio is waiting to upload, so a frame never delays a segment: the
 * capture tells it when its own queue changes. Full, it drops the oldest
 * frame. A failed upload stops it for good and says why, and the study turns
 * itself off; the recording is never touched.
 */

export const FRAME_QUEUE_CAPACITY = 30;

export interface QueuedFrame {
  sequence: number;
  offsetMs: number;
  blob: Blob;
}

export interface FrameQueueOptions<Result> {
  send: (frame: QueuedFrame) => Promise<Result>;
  /** Whether the capture has audio waiting to upload. */
  audioWaiting: () => boolean;
  onSent?: (result: Result) => void;
  onFailed?: (error: unknown) => void;
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

  private sending = false;

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
      sending: this.sending,
      failed: this.failed,
    };
  }

  enqueue(frame: QueuedFrame) {
    if (this.failed || this.disposed) {
      return;
    }
    this.frames.push(frame);
    while (this.frames.length > this.capacity) {
      this.frames.shift();
      this.dropped += 1;
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

  /** Drops what is queued and sends nothing more. */
  dispose() {
    this.disposed = true;
    this.frames.length = 0;
    this.changed();
    this.settleIdle();
  }

  private isIdle() {
    return this.frames.length === 0 && !this.sending;
  }

  private pump() {
    if (this.sending || this.failed || this.disposed || this.frames.length === 0) {
      this.settleIdle();
      return;
    }
    if (this.options.audioWaiting()) {
      return;
    }
    const frame = this.frames.shift() as QueuedFrame;
    this.sending = true;
    this.changed();
    void this.options
      .send(frame)
      .then(
        (result) => {
          this.sending = false;
          if (!this.disposed) {
            this.options.onSent?.(result);
          }
        },
        (error: unknown) => {
          this.sending = false;
          this.failed = true;
          this.frames.length = 0;
          if (!this.disposed) {
            this.options.onFailed?.(error);
          }
        },
      )
      .finally(() => {
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
