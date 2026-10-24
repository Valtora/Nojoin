// @vitest-environment node

import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { crc16, crc8, encodeFlac, FLAC_MIN_FRAMES } from "./flacEncoder";
import { decodeFlac, goldenPcm } from "./testFlacDecoder";

/**
 * The golden fixture lives with the backend, whose test decodes it with
 * libFLAC and compares every sample against the same generator. Together the
 * two tests pin the encoder to a stream the real decoder accepts. After an
 * intended change to the encoder's output, regenerate it with
 * `UPDATE_FLAC_GOLDEN=1 npm run test -- --run src/lib/capture/pcm` and run the
 * backend test before committing.
 */
const GOLDEN_FIXTURE = resolve(
  __dirname,
  "../../../../../backend/tests/fixtures/browser_capture_golden.flac",
);

const seeded = (seed: number) => {
  let state = seed;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
};

const interleave = (frames: number, channels: number, sample: (frame: number, channel: number) => number) => {
  const pcm = new Int16Array(frames * channels);
  for (let frame = 0; frame < frames; frame += 1) {
    for (let channel = 0; channel < channels; channel += 1) {
      pcm[frame * channels + channel] = Math.max(-32768, Math.min(32767, Math.round(sample(frame, channel))));
    }
  }
  return pcm;
};

describe("FLAC encoder", () => {
  it("computes the FLAC CRCs", () => {
    const check = new TextEncoder().encode("123456789");
    expect(crc8(check)).toBe(0xf4);
    expect(crc16(check)).toBe(0xfee8);
  });

  it("reproduces the golden fixture byte for byte", () => {
    const encoded = encodeFlac(goldenPcm(), 2, 16_000);
    if (process.env.UPDATE_FLAC_GOLDEN === "1") {
      writeFileSync(GOLDEN_FIXTURE, encoded);
    }
    expect(Buffer.from(encoded).equals(readFileSync(GOLDEN_FIXTURE))).toBe(true);
  });

  it.each([
    ["a full 2 s segment", 32_000],
    ["one exact block", 4_096],
    ["a block and a bit", 4_097],
    ["an odd tail", 3_329],
    ["the shortest legal stream", FLAC_MIN_FRAMES],
  ])("round-trips %s losslessly", (_name, frames) => {
    const random = seeded(frames);
    const pcm = interleave(frames, 2, (frame, channel) =>
      channel === 0
        ? 9000 * Math.sin(frame / 7) + 1500 * Math.sin(frame / 2.3) + (random() - 0.5) * 600
        : 3000 * Math.sin(frame / 31) * Math.sin(frame / 700) + (random() - 0.5) * 80,
    );

    const decoded = decodeFlac(encodeFlac(pcm, 2, 16_000));

    expect(decoded.sampleRate).toBe(16_000);
    expect(decoded.channels).toBe(2);
    expect(decoded.bitsPerSample).toBe(16);
    expect(decoded.totalFrames).toBe(frames);
    expect(decoded.samples).toEqual(pcm);
  });

  it.each([
    ["white noise, which only verbatim can hold", (r: () => number) => (r() * 2 - 1) * 32767],
    ["full-scale alternation", (_r: () => number, frame: number) => (frame % 2 ? 32767 : -32768)],
    ["a constant", () => -1234],
    ["a single click in silence", (_r: () => number, frame: number) => (frame === 2_000 ? 32767 : 0)],
  ])("round-trips %s", (_name, generator) => {
    const random = seeded(7);
    const pcm = interleave(10_000, 2, (frame) => generator(random, frame));

    expect(decodeFlac(encodeFlac(pcm, 2, 16_000)).samples).toEqual(pcm);
  });

  it("numbers frames past 127, which takes two bytes", () => {
    // 130 blocks: the last frame numbers need FLAC's two-byte UTF-8 form. A
    // quiet signal keeps the bit-level test decoder fast.
    const frames = 130 * 4_096 + 5;
    const pcm = interleave(frames, 1, (frame) => (frame % 4_096 === 0 ? 3 : 0));

    const decoded = decodeFlac(encodeFlac(pcm, 1, 16_000));

    expect(decoded.totalFrames).toBe(frames);
    expect(decoded.samples).toEqual(pcm);
  });

  it("spends almost nothing on a silent channel", () => {
    const random = seeded(3);
    const speech = interleave(32_000, 2, (frame, channel) =>
      channel === 0 ? 0 : 6000 * Math.sin(frame / 5) + (random() - 0.5) * 300,
    );
    const microphoneOnly = interleave(32_000, 1, (frame) => speech[frame * 2 + 1]);

    const stereoBytes = encodeFlac(speech, 2, 16_000).length;
    const monoBytes = encodeFlac(microphoneOnly, 1, 16_000).length;

    expect(stereoBytes - monoBytes).toBeLessThan(200);
  });

  it("never writes more than PCM plus framing", () => {
    const random = seeded(11);
    const noise = interleave(32_000, 2, () => (random() * 2 - 1) * 32767);

    expect(encodeFlac(noise, 2, 16_000).length).toBeLessThan(noise.byteLength * 1.01);
  });

  it("refuses a stream shorter than one minimum block", () => {
    expect(() => encodeFlac(new Int16Array((FLAC_MIN_FRAMES - 1) * 2), 2, 16_000)).toThrow();
  });
});
