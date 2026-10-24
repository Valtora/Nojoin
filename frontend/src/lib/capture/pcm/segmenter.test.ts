// @vitest-environment node

import { describe, expect, it } from "vitest";

import { PcmSegmenter } from "./segmenter";

const ramp = (from: number, frames: number) =>
  Float32Array.from({ length: frames }, (_, index) => ((from + index) % 1000) / 1000);

describe("PcmSegmenter", () => {
  it("cuts exact segments across any block boundaries, in order", () => {
    const segmenter = new PcmSegmenter(1_000, 2);
    const segments: Int16Array[] = [];
    let fed = 0;
    for (const size of [1, 999, 1_000, 1, 2_500, 333, 166]) {
      const left = ramp(fed, size);
      const right = left.map((value) => -value);
      segments.push(...segmenter.push([left, right]));
      fed += size;
    }

    expect(fed).toBe(5_000);
    expect(segments).toHaveLength(5);
    expect(segmenter.bufferedFrames).toBe(0);
    const joined = segments.flatMap((segment) => Array.from(segment));
    for (let frame = 0; frame < fed; frame += 1) {
      const expected = Math.round(((frame % 1000) / 1000) * 0x7fff);
      expect(joined[frame * 2]).toBe(expected);
      // `|| 0` turns the -0 of the first frame into the 0 an Int16Array holds.
      expect(joined[frame * 2 + 1]).toBe(-Math.round(((frame % 1000) / 1000) * 0x8000) || 0);
    }
  });

  it("returns the unfinished segment on flush, and nothing when empty", () => {
    const segmenter = new PcmSegmenter(1_000, 1);
    expect(segmenter.flush()).toBeNull();
    segmenter.push([new Float32Array(250).fill(0.25)]);

    const tail = segmenter.flush();

    expect(tail).toHaveLength(250);
    expect(segmenter.flush()).toBeNull();
  });

  it("clips out-of-range samples instead of wrapping them", () => {
    const segmenter = new PcmSegmenter(4, 1);
    const [segment] = segmenter.push([Float32Array.from([1.5, -1.5, 1, -1])]);
    expect(Array.from(segment)).toEqual([32767, -32768, 32767, -32768]);
  });

  it("fills a missing channel with silence", () => {
    const segmenter = new PcmSegmenter(2, 2);
    const [segment] = segmenter.push([Float32Array.from([0.5, 0.5])]);
    expect(Array.from(segment)).toEqual([16384, 0, 16384, 0]);
  });
});
