import { describe, expect, it } from "vitest";

import { StreamingResampler } from "./resampler";

const seeded = (seed: number) => {
  let state = seed;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
};

const sine = (frames: number, frequency: number, rate: number, amplitude = 0.8) =>
  Float32Array.from({ length: frames }, (_, index) =>
    amplitude * Math.sin((2 * Math.PI * frequency * index) / rate),
  );

/** Runs a signal through in blocks of random size and joins the output. */
const resampleInBlocks = (
  input: Float32Array[],
  inputRate: number,
  outputRate: number,
  seed: number,
) => {
  const resampler = new StreamingResampler(inputRate, outputRate, input.length);
  const random = seeded(seed);
  const pieces: Float32Array[][] = [];
  let offset = 0;
  while (offset < input[0].length) {
    const size = Math.min(input[0].length - offset, 1 + Math.floor(random() * 5000));
    pieces.push(resampler.process(input.map((channel) => channel.subarray(offset, offset + size))));
    offset += size;
  }
  pieces.push(resampler.flush());
  return input.map((_, channel) => {
    const total = pieces.reduce((sum, piece) => sum + piece[channel].length, 0);
    const joined = new Float32Array(total);
    let position = 0;
    for (const piece of pieces) {
      joined.set(piece[channel], position);
      position += piece[channel].length;
    }
    return joined;
  });
};

const rms = (signal: Float32Array, from: number, to: number) => {
  let sum = 0;
  for (let index = from; index < to; index += 1) {
    sum += signal[index] * signal[index];
  }
  return Math.sqrt(sum / (to - from));
};

describe("StreamingResampler", () => {
  it.each([
    [48_000, 96_000, 1],
    [48_000, 96_001, 2],
    [44_100, 88_200, 3],
    [44_100, 123_457, 4],
  ])(
    "produces one output frame per output time from %i Hz, %i frames",
    (inputRate, frames, seed) => {
      const output = resampleInBlocks([new Float32Array(frames)], inputRate, 16_000, seed);
      expect(output[0].length).toBe(Math.ceil((frames * 16_000) / inputRate));
    },
  );

  it("gives the same samples however the input is split into blocks", () => {
    const input = [sine(48_000, 440, 48_000), sine(48_000, 1_234, 48_000)];
    const whole = resampleInBlocks(input, 48_000, 16_000, 1);
    const split = resampleInBlocks(input, 48_000, 16_000, 99);
    expect(split[0]).toEqual(whole[0]);
    expect(split[1]).toEqual(whole[1]);
  });

  it("passes speech frequencies at full level", () => {
    const [output] = resampleInBlocks([sine(96_000, 1_000, 48_000)], 48_000, 16_000, 5);
    // A full-scale sine of amplitude 0.8 has an RMS of 0.8 / sqrt(2).
    expect(rms(output, 2_000, 30_000)).toBeCloseTo(0.8 / Math.SQRT2, 2);
  });

  it("holds a constant level exactly", () => {
    const [output] = resampleInBlocks([new Float32Array(48_000).fill(0.5)], 48_000, 16_000, 6);
    for (let index = 100; index < output.length - 100; index += 1) {
      expect(output[index]).toBeCloseTo(0.5, 6);
    }
  });

  it.each([44_100, 48_000])("removes what 16 kHz cannot carry from %i Hz", (inputRate) => {
    // 11 kHz folds back to 5 kHz at 16 kHz unless it is filtered out first.
    const [output] = resampleInBlocks([sine(inputRate * 2, 11_000, inputRate)], inputRate, 16_000, 7);
    expect(rms(output, 2_000, 30_000)).toBeLessThan(0.005);
  });

  it("passes audio through untouched at the output rate", () => {
    const resampler = new StreamingResampler(16_000, 16_000, 2);
    const block = [sine(1_000, 300, 16_000), sine(1_000, 700, 16_000)];
    const output = resampler.process(block);
    expect(output).toEqual(block);
    expect(resampler.flush()[0]).toHaveLength(0);
  });
});
