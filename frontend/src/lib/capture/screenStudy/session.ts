/**
 * One capture's screen study (#653): samples the shared screen in time with
 * the audio and sends the frames that changed.
 *
 * The capture ticks it once per second of recorded audio (twice at 2 Hz). On
 * each tick it asks the frame reader for the newest frame's colour grid,
 * keeps the frame when the change rule says so, and queues its JPEG with the
 * audio position it was taken at. Paused, it takes nothing.
 *
 * Each frame carries its tick, counted from 0, and `lost_before`: the ticks
 * since the previous stored frame that were not sampled (the reader was still
 * busy, had no frame yet or failed) or whose kept frame was dropped or failed
 * to upload. A tick whose screen had not changed is not a loss, so the
 * analysis can tell a still screen from a gap.
 *
 * It works on a clone of the shared video track, never the original, so
 * stopping it leaves the share and the recording running. Anything that goes
 * wrong stops sampling and says why; the recording is never touched. Nothing
 * it holds reaches browser storage.
 */

import { AxiosError } from "axios";

import {
  deleteSpeakerFrames,
  uploadSpeakerFrame,
  type SpeakerFrameSummary,
  type SpeakerFrameUpload,
  type SpeakerFrameUploadResult,
} from "@/lib/api/speakerFrames";
import type { RecordingId } from "@/types";

import type { AudioPosition } from "../recorder";
import { ChangeRule } from "./changeRule";
import { createWorkerFrameGrabber, type FrameGrabber } from "./frameGrabber";
import { FrameQueue } from "./frameQueue";
import { useScreenStudyStore, type ScreenStudyState } from "./store";

/** How long queued frames may keep sending after the recording stops. */
export const SCREEN_STUDY_DRAIN_MS = 20_000;

/**
 * How long Delete waits for the aborted upload to settle before deleting. The
 * server refuses every frame for the recording once Delete reaches it, so a
 * frame still in flight after this is refused rather than kept.
 */
const DELETE_SETTLE_MS = 5_000;

/** The server's bound on `grab_delay_ms`. */
const MAX_GRAB_DELAY_MS = 3_600_000;

type SessionStatus = "sampling" | "paused" | "stopped" | "ended";

export interface ScreenStudySessionOptions {
  recordingId: RecordingId;
  /** The shared screen's own video track. The session works on a clone. */
  displayTrack: MediaStreamTrack;
  /** Whether the capture has audio waiting to upload; frames wait for it. */
  audioWaiting: () => boolean;
  upload?: (
    recordingId: RecordingId,
    frame: SpeakerFrameUpload,
    signal: AbortSignal,
  ) => Promise<SpeakerFrameUploadResult>;
  remove?: (recordingId: RecordingId) => Promise<{ deleted: number }>;
  createGrabber?: (clone: MediaStreamTrack) => FrameGrabber;
  /** Milliseconds, for the 15 s keep-alive. */
  now?: () => number;
  /** The same clock the reader stamps its grabs with. */
  tickClock?: () => number;
  drainMs?: number;
}

const summaryPatch = (summary: SpeakerFrameSummary): Partial<ScreenStudyState> => ({
  frames: summary.frames,
  bytes: summary.bytes,
  deleteAfter: summary.delete_after,
});

export const describeScreenStudyFailure = (error: unknown): string => {
  if (error instanceof AxiosError) {
    const status = error.response?.status;
    const detail = error.response?.data?.detail;
    if (status === 507) {
      return "The screen study has reached its storage limit, so sampling stopped.";
    }
    if (status === 429) {
      return "The server refused frames this fast, so sampling stopped.";
    }
    if (typeof detail === "string" && detail) {
      return `Sampling stopped: ${detail}`;
    }
    return "A frame could not be sent, so sampling stopped.";
  }
  if (error instanceof Error && error.message) {
    return `Sampling stopped: ${error.message}`;
  }
  return "Sampling stopped after an error.";
};

export class ScreenStudySession {
  readonly recordingId: RecordingId;

  private statusValue: SessionStatus = "sampling";

  private readonly options: ScreenStudySessionOptions;

  private readonly rule = new ChangeRule();

  private readonly queue: FrameQueue<SpeakerFrameUploadResult>;

  private readonly now: () => number;

  private readonly tickClock: () => number;

  private grabber: FrameGrabber | null = null;

  private busy = false;

  private finishing: Promise<void> | null = null;

  /** Ticks seen while sampling; the next tick's index. */
  private ticks = 0;

  /** Lost ticks not yet counted into a stored frame's `lost_before`. */
  private lostTicks: number[] = [];

  constructor(options: ScreenStudySessionOptions) {
    this.options = options;
    this.recordingId = options.recordingId;
    this.now = options.now ?? (() => Date.now());
    this.tickClock = options.tickClock ?? (() => performance.timeOrigin + performance.now());
    const upload = options.upload ?? uploadSpeakerFrame;
    this.queue = new FrameQueue<SpeakerFrameUploadResult>({
      send: (frame, signal) =>
        upload(this.recordingId, { ...frame, lostBefore: this.takeLost(frame.tick) }, signal),
      audioWaiting: options.audioWaiting,
      onSent: (result) => this.publish(summaryPatch(result)),
      onFailed: (error, frame) => {
        this.lostTicks.push(frame.tick);
        this.fail(error);
      },
      onDropped: (frame) => this.lostTicks.push(frame.tick),
      onChange: (state) => this.publish({ queued: state.queued, dropped: state.dropped }),
    });
  }

  get status(): SessionStatus {
    return this.statusValue;
  }

  /** Clones the track and starts the frame reader. Throws if the browser cannot. */
  start() {
    const clone = this.options.displayTrack.clone();
    try {
      this.grabber = (this.options.createGrabber ?? createWorkerFrameGrabber)(clone);
    } catch (error: unknown) {
      clone.stop();
      throw error;
    }
    useScreenStudyStore.getState().update({
      recordingId: this.recordingId,
      status: "sampling",
      frames: 0,
      bytes: 0,
      deleteAfter: null,
      queued: 0,
      dropped: 0,
      error: null,
      deleting: false,
    });
  }

  tick = (position: AudioPosition) => {
    if (this.statusValue !== "sampling" || !this.grabber) {
      return;
    }
    const tick = this.ticks;
    this.ticks += 1;
    if (this.busy) {
      this.lostTicks.push(tick);
      return;
    }
    const tickAt = this.tickClock();
    this.busy = true;
    void this.sampleAt(position, tick, tickAt).finally(() => {
      this.busy = false;
    });
  };

  pause() {
    if (this.statusValue === "sampling") {
      this.statusValue = "paused";
      this.publish({ status: "paused" });
    }
  }

  resume() {
    if (this.statusValue === "paused") {
      this.statusValue = "sampling";
      this.publish({ status: "sampling" });
    }
  }

  audioQueueChanged() {
    this.queue.audioQueueChanged();
  }

  /** Stop: no more frames are taken. Those already queued still go. */
  stopSampling(error?: string) {
    if (this.statusValue === "stopped" || this.statusValue === "ended") {
      if (error) {
        this.publish({ error });
      }
      return;
    }
    this.statusValue = "stopped";
    this.releaseGrabber();
    this.publish({ status: "stopped", ...(error ? { error } : {}) });
  }

  /**
   * The recording stopped: stop sampling, give the queue up to 20 s to empty,
   * then let go. Resolves when it has.
   */
  finish(): Promise<void> {
    if (!this.finishing) {
      this.stopSampling();
      this.finishing = this.queue
        .drain(this.options.drainMs ?? SCREEN_STUDY_DRAIN_MS)
        .then(() => this.end());
    }
    return this.finishing;
  }

  /** Discarded, taken over or unloaded: drop everything at once. */
  abort() {
    this.end();
  }

  /**
   * Stop sampling, abort the upload in flight, drop the queue and delete this
   * meeting's frames on the server, which refuses any frame for it from then on.
   */
  async deleteFrames() {
    this.stopSampling();
    this.queue.dispose();
    this.publish({ deleting: true });
    try {
      await this.queue.drain(DELETE_SETTLE_MS);
      await (this.options.remove ?? deleteSpeakerFrames)(this.recordingId);
      this.publish({
        status: "deleted",
        frames: 0,
        bytes: 0,
        queued: 0,
        deleting: false,
      });
    } catch (error: unknown) {
      this.publish({ deleting: false });
      throw error;
    }
  }

  private async sampleAt(position: AudioPosition, tick: number, tickAt: number) {
    const grabber = this.grabber;
    if (!grabber) {
      this.lostTicks.push(tick);
      return;
    }
    try {
      const sample = await grabber.sample();
      if (!sample || this.statusValue !== "sampling") {
        this.lostTicks.push(tick);
        return;
      }
      const at = this.now();
      if (!this.rule.shouldKeep(sample.grid, at)) {
        return;
      }
      const blob = await grabber.encode();
      if (this.isEnded()) {
        return;
      }
      this.rule.kept(sample.grid, at);
      this.queue.enqueue({
        sequence: position.sequence,
        offsetMs: position.offsetMs,
        blob,
        tick,
        frameTsUs: Math.round(sample.timestampUs),
        grabDelayMs: Math.min(
          MAX_GRAB_DELAY_MS,
          Math.max(0, Math.round(sample.grabbedAt - tickAt)),
        ),
      });
    } catch (error: unknown) {
      this.lostTicks.push(tick);
      if (this.statusValue === "sampling" || this.statusValue === "paused") {
        this.fail(error);
      }
    }
  }

  /** The lost ticks before `tick`, counted once into the frame about to go. */
  private takeLost(tick: number): number {
    const before = this.lostTicks.filter((lost) => lost < tick);
    this.lostTicks = this.lostTicks.filter((lost) => lost >= tick);
    return before.length;
  }

  /** Read fresh after an await: `end` may have run meanwhile. */
  private isEnded() {
    return this.statusValue === "ended";
  }

  private fail(error: unknown) {
    console.warn("[screen-study] sampling stopped", error);
    this.stopSampling(describeScreenStudyFailure(error));
  }

  private releaseGrabber() {
    const grabber = this.grabber;
    this.grabber = null;
    grabber?.stop();
  }

  private end() {
    this.statusValue = "ended";
    this.releaseGrabber();
    this.queue.dispose();
    if (useScreenStudyStore.getState().recordingId === this.recordingId) {
      useScreenStudyStore.getState().reset();
    }
  }

  /** Updates the live controls, unless another capture's study now owns them. */
  private publish(patch: Partial<ScreenStudyState>) {
    const store = useScreenStudyStore.getState();
    if (store.recordingId === this.recordingId) {
      store.update(patch);
    }
  }
}

export const startScreenStudySession = (options: ScreenStudySessionOptions) => {
  const session = new ScreenStudySession(options);
  session.start();
  return session;
};
