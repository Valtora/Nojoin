import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { encodeFlac } from "./flacEncoder";
import { joinFlacSegments } from "./flacJoin";
import { decodeFlac, goldenPcm } from "./testFlacDecoder";

/**
 * Checked by the backend with libFLAC, as the encoder's golden fixture is.
 * After an intended change to the joiner's output, regenerate it with
 * `UPDATE_FLAC_GOLDEN=1 npm run test -- --run src/lib/capture/pcm` and run the
 * backend test before committing.
 */
const JOINED_GOLDEN_FIXTURE = resolve(
  __dirname,
  "../../../../../backend/tests/fixtures/browser_capture_joined_golden.flac",
);

/** The golden samples cut where the backend test expects them. */
const GOLDEN_CUTS = [2_000, 2_500, 500];

const seeded = (seed: number) => {
  let state = seed;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
};

/** Speech-like stereo PCM, different for every seed. */
const pcmOf = (frames: number, seed: number) => {
  const random = seeded(seed);
  const pcm = new Int16Array(frames * 2);
  for (let frame = 0; frame < frames; frame += 1) {
    pcm[frame * 2] = Math.round(8000 * Math.sin((frame + seed) / 9) + (random() - 0.5) * 400);
    pcm[frame * 2 + 1] = Math.round(2000 * Math.sin(frame / 40) + (random() - 0.5) * 60);
  }
  return pcm;
};

const concat = (parts: Int16Array[]) => {
  const all = new Int16Array(parts.reduce((total, part) => total + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    all.set(part, offset);
    offset += part.length;
  }
  return all;
};

/** Cuts interleaved stereo PCM into consecutive pieces of the given frame counts. */
const cut = (pcm: Int16Array, frameCounts: number[]) => {
  let frame = 0;
  return frameCounts.map((count) => {
    const piece = pcm.subarray(frame * 2, (frame + count) * 2);
    frame += count;
    return piece;
  });
};

describe("joining FLAC segments", () => {
  it("reproduces the joined golden fixture byte for byte", () => {
    const segments = cut(goldenPcm(), GOLDEN_CUTS).map((piece) => encodeFlac(piece, 2, 16_000));
    const joined = joinFlacSegments(segments);
    if (process.env.UPDATE_FLAC_GOLDEN === "1" && joined) {
      writeFileSync(JOINED_GOLDEN_FIXTURE, joined.bytes);
    }
    expect(joined).not.toBeNull();
    expect(Buffer.from(joined!.bytes).equals(readFileSync(JOINED_GOLDEN_FIXTURE))).toBe(true);
  });

  it("holds exactly the samples of capture segments placed end to end", () => {
    // Full 2 s segments end on a 3,328-frame block, so the joined stream mixes
    // block sizes and numbers its frames by sample.
    const pieces = [pcmOf(32_000, 1), pcmOf(32_000, 2), pcmOf(32_000, 3), pcmOf(1_234, 4)];

    const joined = joinFlacSegments(pieces.map((piece) => encodeFlac(piece, 2, 16_000)));
    const decoded = decodeFlac(joined!.bytes);

    expect(joined).toMatchObject({ frames: 97_234, sampleRate: 16_000, segments: 4 });
    expect(decoded).toMatchObject({
      sampleRate: 16_000,
      channels: 2,
      bitsPerSample: 16,
      totalFrames: 97_234,
      minBlockSize: 3_328,
      maxBlockSize: 4_096,
    });
    expect(decoded.samples).toEqual(concat(pieces));
  });

  it("keeps frame numbers when every block but the last is one size", () => {
    const pieces = [pcmOf(8_192, 5), pcmOf(4_096, 6), pcmOf(100, 7)];

    const decoded = decodeFlac(
      joinFlacSegments(pieces.map((piece) => encodeFlac(piece, 2, 16_000)))!.bytes,
    );

    expect(decoded).toMatchObject({ minBlockSize: 4_096, maxBlockSize: 4_096, totalFrames: 12_388 });
    expect(decoded.samples).toEqual(concat(pieces));
  });

  it("numbers samples past the two-byte range", () => {
    // Sample numbers above 65,535 take FLAC's four-byte UTF-8 form. Quiet
    // audio keeps the bit-level test decoder fast.
    const quiet = Array.from({ length: 40 }, () => new Int16Array(32_000 * 2));

    const decoded = decodeFlac(
      joinFlacSegments(quiet.map((piece) => encodeFlac(piece, 2, 16_000)))!.bytes,
    );

    expect(decoded.totalFrames).toBe(1_280_000);
  });

  it("leaves out a damaged segment and one in another format, joining the rest", () => {
    const pieces = [pcmOf(32_000, 8), pcmOf(32_000, 9), pcmOf(32_000, 10)];
    const encoded = pieces.map((piece) => encodeFlac(piece, 2, 16_000));
    const truncated = encoded[1].subarray(0, encoded[1].length - 100);
    const corrupted = Uint8Array.from(encoded[1]);
    corrupted[corrupted.length >> 1] ^= 0x10;
    const mono = encodeFlac(pcmOf(16_000, 11).subarray(0, 16_000), 1, 16_000);
    const fast = encodeFlac(pcmOf(32_000, 12), 2, 48_000);

    const joined = joinFlacSegments([
      encoded[0],
      truncated,
      corrupted,
      mono,
      fast,
      new TextEncoder().encode("not audio"),
      encoded[2],
    ]);

    expect(joined?.segments).toBe(2);
    expect(decodeFlac(joined!.bytes).samples).toEqual(concat([pieces[0], pieces[2]]));
  });

  it("gives nothing when no segment can be read", () => {
    expect(joinFlacSegments([])).toBeNull();
    expect(joinFlacSegments([new Uint8Array(64)])).toBeNull();
  });
});
