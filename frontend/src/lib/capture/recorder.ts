import { encodeFlac, FLAC_MIME_TYPE, FLAC_MIN_FRAMES } from "./pcm/flacEncoder";
import { StreamingResampler } from "./pcm/resampler";
import { PcmSegmenter } from "./pcm/segmenter";
import type { PcmSource } from "./pcm/tapSource";

/**
 * Turns the mixer's continuous audio into numbered, sample-exact FLAC segments.
 *
 * Audio comes from an AudioWorklet tap on the mixer, is resampled to the 16 kHz
 * the backend works in, cut into segments by counting frames, and encoded as
 * FLAC. Nothing restarts between segments, so they join with no gap and the
 * backend never has to transcode them (issue #417).
 */

export interface RecordedChunk {
  sequence: number;
  blob: Blob;
  /** Exact length of the audio in the segment. */
  durationSeconds: number;
}

export interface RecorderStallInfo {
  /** Milliseconds since the recorder last received audio. */
  sinceLastChunkMs: number;
  /**
   * Always false for the PCM recorder, which has no segment to abandon. Kept so
   * the stall report keeps its shape for the coverage classifier.
   */
  abandonedSegment: boolean;
}

export interface CreateBrowserRecorderOptions {
  source: PcmSource;
  startSequence?: number;
  onChunk: (chunk: RecordedChunk) => void | Promise<void>;
  onError?: (error: Error) => void;
  onStall?: (info: RecorderStallInfo) => void;
  segmentSeconds?: number;
  outputSampleRate?: number;
  stallTimeoutMs?: number;
  now?: () => number;
  encode?: typeof encodeFlac;
}

export type RecorderState = "inactive" | "recording" | "paused";

/** Rate the backend's live and final pipelines work in. */
export const CAPTURE_SAMPLE_RATE_HZ = 16_000;

export const CAPTURE_SEGMENT_SECONDS = 2;

/** No audio from the tap for this long while recording means the tab was frozen. */
const DEFAULT_STALL_TIMEOUT_MS = 6_000;

const STALL_CHECK_INTERVAL_MS = 2_000;

interface Chain {
  resampler: StreamingResampler;
  segmenter: PcmSegmenter;
}

export class BrowserRecorder {
  private readonly source: PcmSource;

  private readonly onChunk: CreateBrowserRecorderOptions["onChunk"];

  private readonly onError?: CreateBrowserRecorderOptions["onError"];

  private readonly onStall?: CreateBrowserRecorderOptions["onStall"];

  private readonly outputSampleRate: number;

  private readonly framesPerSegment: number;

  private readonly stallTimeoutMs: number;

  private readonly now: () => number;

  private readonly encode: typeof encodeFlac;

  private nextSequence: number;

  private stateValue: RecorderState = "inactive";

  private chain: Chain | null = null;

  private unsubscribe: (() => void) | null = null;

  private operationQueue: Promise<unknown> = Promise.resolve();

  private lastAudioAt = 0;

  private lastChunkAt = 0;

  private stallTimerId: ReturnType<typeof setInterval> | null = null;

  constructor(options: CreateBrowserRecorderOptions) {
    this.source = options.source;
    this.onChunk = options.onChunk;
    this.onError = options.onError;
    this.onStall = options.onStall;
    this.outputSampleRate = options.outputSampleRate ?? CAPTURE_SAMPLE_RATE_HZ;
    this.framesPerSegment = Math.round(
      (options.segmentSeconds ?? CAPTURE_SEGMENT_SECONDS) * this.outputSampleRate,
    );
    this.stallTimeoutMs = options.stallTimeoutMs ?? DEFAULT_STALL_TIMEOUT_MS;
    this.now = options.now ?? (() => Date.now());
    this.encode = options.encode ?? encodeFlac;
    this.nextSequence = options.startSequence ?? 0;
  }

  get state() {
    return this.stateValue;
  }

  /**
   * The sequence the next segment is numbered with. While paused, where the
   * audio recorded after a resume starts.
   */
  get nextSegmentSequence() {
    return this.nextSequence;
  }

  /** Timestamp of the most recent emitted segment, for coverage tracking. */
  get lastChunkEmittedAt() {
    return this.lastChunkAt;
  }

  start() {
    if (this.stateValue !== "inactive" || this.unsubscribe) {
      return;
    }
    this.unsubscribe = this.source.subscribe(this.handleBlock);
    this.beginRecording();
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
      await this.source.flush();
      this.stateValue = "paused";
      this.finishChain(true);
    });
  }

  resume() {
    if (this.stateValue !== "paused") {
      return Promise.resolve();
    }
    return this.enqueueOperation(async () => {
      if (this.stateValue === "paused") {
        // The tap keeps rendering during pause. Drop its partial block while
        // still paused; PCM precedes the acknowledgement on the same port. A
        // suspended context renders nothing, so a timeout has nothing to drop.
        await this.source.flush();
        this.beginRecording();
      }
    });
  }

  stop(options: { emitTail?: boolean } = {}) {
    const emitTail = options.emitTail !== false;
    this.stopStallWatchdog();
    return this.enqueueOperation(async () => {
      this.stopStallWatchdog();
      if (this.stateValue === "recording") {
        await this.source.flush();
      }
      this.stateValue = "inactive";
      this.finishChain(emitTail);
      this.unsubscribe?.();
      this.unsubscribe = null;
    });
  }

  private beginRecording() {
    this.chain = {
      resampler: new StreamingResampler(
        this.source.sampleRate,
        this.outputSampleRate,
        this.source.channelCount,
      ),
      segmenter: new PcmSegmenter(this.framesPerSegment, this.source.channelCount),
    };
    this.stateValue = "recording";
    // The paused interval is not a stall, and a stale marker would trip the
    // watchdog on its first tick.
    this.lastAudioAt = this.now();
    this.startStallWatchdog();
  }

  private handleBlock = (block: Float32Array[]) => {
    if (this.stateValue !== "recording" || !this.chain) {
      return;
    }
    this.lastAudioAt = this.now();
    const resampled = this.chain.resampler.process(block);
    for (const segment of this.chain.segmenter.push(resampled)) {
      this.emitSegment(segment);
    }
  };

  /** Drains the resampler and emits what is left, then drops the chain. */
  private finishChain(emitTail: boolean) {
    const chain = this.chain;
    this.chain = null;
    if (!chain || !emitTail) {
      return;
    }
    for (const segment of chain.segmenter.push(chain.resampler.flush())) {
      this.emitSegment(segment);
    }
    const tail = chain.segmenter.flush();
    // FLAC has no stream shorter than 16 frames (1 ms), so a sliver that short
    // at a pause or stop is dropped.
    if (tail && tail.length / this.source.channelCount >= FLAC_MIN_FRAMES) {
      this.emitSegment(tail);
    }
  }

  private emitSegment(pcm: Int16Array) {
    const channels = this.source.channelCount;
    const frames = pcm.length / channels;
    let blob: Blob;
    try {
      const flac = this.encode(pcm, channels, this.outputSampleRate);
      blob = new Blob([flac.buffer as ArrayBuffer], { type: FLAC_MIME_TYPE });
    } catch (error: unknown) {
      this.onError?.(
        error instanceof Error ? error : new Error("Failed to encode a recording segment."),
      );
      return;
    }
    const sequence = this.nextSequence;
    this.nextSequence += 1;
    this.lastChunkAt = this.now();
    void Promise.resolve(
      this.onChunk({ sequence, blob, durationSeconds: frames / this.outputSampleRate }),
    ).catch((error) => {
      this.onError?.(
        error instanceof Error
          ? error
          : new Error("The browser recorder failed to queue a segment."),
      );
    });
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
   * Reports a capture that has gone quiet while still recording.
   *
   * A suspended tab (OS sleep, browser tab freezing) stops the audio thread,
   * so the tap stops delivering without any error. Audio resumes by itself
   * when the tab thaws; this only makes the gap visible (issue #166).
   */
  private checkForStall() {
    if (this.stateValue !== "recording") {
      return;
    }
    const sinceLastChunkMs = this.now() - this.lastAudioAt;
    if (sinceLastChunkMs < this.stallTimeoutMs) {
      return;
    }
    this.lastAudioAt = this.now();
    this.onStall?.({ sinceLastChunkMs, abandonedSegment: false });
  }

  private enqueueOperation<T>(operation: () => Promise<T>) {
    const next = this.operationQueue.then(operation, operation);
    this.operationQueue = next.then(
      () => undefined,
      () => undefined,
    );
    return next;
  }
}

export const createBrowserRecorder = (options: CreateBrowserRecorderOptions) =>
  new BrowserRecorder(options);
