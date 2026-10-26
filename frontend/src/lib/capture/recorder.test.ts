import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StreamingResampler } from "./pcm/resampler";
import type { PcmBlockListener, PcmSource } from "./pcm/tapSource";
import { createBrowserRecorder, type RecordedChunk } from "./recorder";

const INPUT_RATE = 48_000;

class FakePcmSource implements PcmSource {
  readonly sampleRate = INPUT_RATE;

  readonly channelCount = 2;

  readonly listeners = new Set<PcmBlockListener>();

  flushes = 0;

  /** Frames the worklet is holding in its unfinished block. */
  private held: Float32Array[] | null = null;

  private rendered = 0;

  subscribe(listener: PcmBlockListener) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** Renders `frames` of a 440 Hz / 220 Hz tone, delivered in 4096-frame blocks. */
  render(frames: number) {
    const left = new Float32Array(frames);
    const right = new Float32Array(frames);
    for (let index = 0; index < frames; index += 1) {
      const time = (this.rendered + index) / INPUT_RATE;
      left[index] = 0.5 * Math.sin(2 * Math.PI * 440 * time);
      right[index] = 0.25 * Math.sin(2 * Math.PI * 220 * time);
    }
    this.rendered += frames;
    let offset = 0;
    while (offset < frames) {
      const take = Math.min(4_096, frames - offset);
      this.deliver([left.subarray(offset, offset + take), right.subarray(offset, offset + take)]);
      offset += take;
    }
  }

  /** Renders without delivering, as the worklet does for a partial block. */
  hold(frames: number) {
    const before = this.listeners;
    const captured: Float32Array[][] = [];
    const listeners = new Set<PcmBlockListener>([(block) => captured.push(block)]);
    Object.defineProperty(this, "listeners", { value: listeners, configurable: true });
    this.render(frames);
    Object.defineProperty(this, "listeners", { value: before, configurable: true });
    this.held = [
      Float32Array.from(captured.flatMap((block) => Array.from(block[0]))),
      Float32Array.from(captured.flatMap((block) => Array.from(block[1]))),
    ];
  }

  deliver(block: Float32Array[]) {
    this.listeners.forEach((listener) => listener(block));
  }

  async flush() {
    this.flushes += 1;
    if (this.held) {
      const held = this.held;
      this.held = null;
      this.deliver(held);
    }
    return true;
  }

  close() {}
}

/** Keeps the PCM instead of encoding it, so tests can read the samples back. */
const rawEncode = (pcm: Int16Array) => new Uint8Array(pcm.slice().buffer);

const samplesOf = async (chunk: RecordedChunk) =>
  new Int16Array(await chunk.blob.arrayBuffer());

describe("PCM recorder", () => {
  let source: FakePcmSource;
  let chunks: RecordedChunk[];

  beforeEach(() => {
    vi.useFakeTimers();
    source = new FakePcmSource();
    chunks = [];
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const createRecorder = (options: Partial<Parameters<typeof createBrowserRecorder>[0]> = {}) =>
    createBrowserRecorder({
      source,
      onChunk: (chunk) => {
        chunks.push(chunk);
      },
      encode: rawEncode,
      ...options,
    });

  it("emits exact 2 s FLAC segments numbered from the start sequence", () => {
    const recorder = createBrowserRecorder({
      source,
      startSequence: 7,
      onChunk: (chunk) => {
        chunks.push(chunk);
      },
    });
    recorder.start();

    source.render(INPUT_RATE * 5);

    expect(chunks.map((chunk) => chunk.sequence)).toEqual([7, 8]);
    expect(chunks.every((chunk) => chunk.durationSeconds === 2)).toBe(true);
    expect(chunks.every((chunk) => chunk.blob.type === "audio/flac")).toBe(true);
  });

  it("joins segments with no gap: together they are the whole resampled stream", async () => {
    const recorder = createRecorder();
    recorder.start();
    source.render(INPUT_RATE * 5 + 1_234);
    await recorder.stop();

    const joined = (await Promise.all(chunks.map(samplesOf))).flatMap((pcm) => Array.from(pcm));
    const reference = new FakePcmSource();
    const resampler = new StreamingResampler(INPUT_RATE, 16_000, 2);
    const parts: Float32Array[][] = [];
    reference.subscribe((block) => parts.push(resampler.process(block)));
    reference.render(INPUT_RATE * 5 + 1_234);
    parts.push(resampler.flush());
    const expectedFrames = parts.reduce((sum, part) => sum + part[0].length, 0);

    expect(joined.length / 2).toBe(expectedFrames);
    expect(chunks.at(-1)?.durationSeconds).toBeCloseTo((expectedFrames - 2 * 32_000) / 16_000, 9);
  });

  it("collects the worklet's unfinished block before a pause", async () => {
    const recorder = createRecorder();
    recorder.start();
    source.render(INPUT_RATE);
    source.hold(1_000);

    await recorder.pause();

    expect(source.flushes).toBe(1);
    expect(chunks).toHaveLength(1);
    // 1 s plus the held frames, resampled.
    expect(chunks[0].durationSeconds).toBeCloseTo((INPUT_RATE + 1_000) / 3 / 16_000, 3);
  });

  it("ignores audio while paused and carries on numbering after a resume", async () => {
    const recorder = createRecorder({ startSequence: 3 });
    recorder.start();
    source.render(INPUT_RATE * 2);
    await recorder.pause();
    source.render(INPUT_RATE * 4);
    recorder.resume();
    await Promise.resolve();
    source.render(INPUT_RATE * 2);
    await recorder.stop();

    // 2 s before the pause and 2 s after it: the paused 4 s were never recorded.
    expect(chunks.map((chunk) => chunk.sequence)).toEqual([3, 4]);
    expect(chunks.reduce((sum, chunk) => sum + chunk.durationSeconds, 0)).toBeCloseTo(4, 2);
  });

  it("drops the unfinished segment when stopped without a tail", async () => {
    const recorder = createRecorder();
    recorder.start();
    source.render(INPUT_RATE * 3);

    await recorder.stop({ emitTail: false });
    source.render(INPUT_RATE * 3);

    expect(chunks).toHaveLength(1);
    expect(source.listeners.size).toBe(0);
    expect(recorder.state).toBe("inactive");
  });

  it("drops a tail too short to be a FLAC stream", async () => {
    const recorder = createRecorder();
    recorder.start();
    source.render(INPUT_RATE * 2 + 30);

    await recorder.stop();

    expect(chunks).toHaveLength(1);
  });

  it("reports a stall when the audio stops arriving while recording", () => {
    const onStall = vi.fn();
    const recorder = createRecorder({ onStall });
    recorder.start();
    source.render(4_096);

    vi.advanceTimersByTime(8_000);

    expect(onStall).toHaveBeenCalledTimes(1);
    expect(onStall.mock.calls[0][0]).toMatchObject({ abandonedSegment: false });
    expect(onStall.mock.calls[0][0].sinceLastChunkMs).toBeGreaterThanOrEqual(6_000);
  });

  it("does not count a pause as a stall", async () => {
    const onStall = vi.fn();
    const recorder = createRecorder({ onStall });
    recorder.start();
    await recorder.pause();

    vi.advanceTimersByTime(60_000);

    expect(onStall).not.toHaveBeenCalled();
  });

  it("reports an encoding failure without using up a sequence number", () => {
    const onError = vi.fn();
    let failures = 1;
    const recorder = createRecorder({
      onError,
      encode: (pcm) => {
        if (failures > 0) {
          failures -= 1;
          throw new Error("encoder broke");
        }
        return rawEncode(pcm);
      },
    });
    recorder.start();

    // Past 4 s, so the second segment completes without a flush.
    source.render(INPUT_RATE * 5);

    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: "encoder broke" }));
    expect(chunks.map((chunk) => chunk.sequence)).toEqual([0]);
  });
});
