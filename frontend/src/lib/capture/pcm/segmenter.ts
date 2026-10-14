/**
 * Cuts a continuous PCM stream into fixed-length segments.
 *
 * Segments are cut by counting frames, never by a timer, so every full segment
 * holds exactly `framesPerSegment` frames and consecutive segments join with
 * no gap and no overlap. The MediaRecorder this replaces was stopped and
 * restarted every 2 s, which dropped about 28 ms of audio at every restart and
 * produced segments whose containers disagreed about their own length (issue
 * #417).
 */

const toInt16 = (value: number) => {
  const clamped = value < -1 ? -1 : value > 1 ? 1 : value;
  return Math.round(clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff);
};

export class PcmSegmenter {
  readonly framesPerSegment: number;

  readonly channels: number;

  private buffer: Int16Array;

  private filled = 0;

  constructor(framesPerSegment: number, channels: number) {
    this.framesPerSegment = framesPerSegment;
    this.channels = channels;
    this.buffer = new Int16Array(framesPerSegment * channels);
  }

  /** Frames waiting in the unfinished segment. */
  get bufferedFrames() {
    return this.filled;
  }

  /** Adds a block, one array per channel, and returns the segments it completes. */
  push(block: Float32Array[]): Int16Array[] {
    const frames = block[0]?.length ?? 0;
    const completed: Int16Array[] = [];
    let read = 0;
    while (read < frames) {
      const take = Math.min(frames - read, this.framesPerSegment - this.filled);
      for (let frame = 0; frame < take; frame += 1) {
        const target = (this.filled + frame) * this.channels;
        for (let channel = 0; channel < this.channels; channel += 1) {
          const source = block[channel];
          this.buffer[target + channel] = source ? toInt16(source[read + frame]) : 0;
        }
      }
      this.filled += take;
      read += take;
      if (this.filled === this.framesPerSegment) {
        completed.push(this.buffer);
        this.buffer = new Int16Array(this.framesPerSegment * this.channels);
        this.filled = 0;
      }
    }
    return completed;
  }

  /** Returns the unfinished segment, trimmed to its length, or null if it is empty. */
  flush(): Int16Array | null {
    if (this.filled === 0) {
      return null;
    }
    const tail = this.buffer.slice(0, this.filled * this.channels);
    this.buffer = new Int16Array(this.framesPerSegment * this.channels);
    this.filled = 0;
    return tail;
  }
}
