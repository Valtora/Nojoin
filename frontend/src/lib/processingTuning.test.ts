import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  PROCESSING_TUNING_KEYS,
  PROCESSING_TUNING_SPECS,
  processingTuningReset,
  validateProcessingTuning,
} from "./processingTuning";

/**
 * Reads key, default, min and max of every TuningSpec in the backend module,
 * which is what the API validates against. A frontend bound that drifts from
 * it would let the page offer values the server refuses.
 */
function readBackendSpecs(): Record<string, [number, number, number]> {
  const source = readFileSync(
    path.resolve(__dirname, "../../../backend/processing/processing_tuning.py"),
    "utf8",
  );
  const keyNames = new Map(
    Array.from(source.matchAll(/^(\w+_KEY) = "(\w+)"$/gm), (m) => [m[1], m[2]]),
  );
  const specs: Record<string, [number, number, number]> = {};
  for (const match of source.matchAll(
    /TuningSpec\((\w+_KEY), ([\d.]+), ([\d.]+), ([\d.]+)/g,
  )) {
    const key = keyNames.get(match[1]);
    expect(key, `${match[1]} is not defined`).toBeDefined();
    specs[key as string] = [Number(match[2]), Number(match[3]), Number(match[4])];
  }
  return specs;
}

describe("processing tuning specs", () => {
  it("mirror the backend's defaults and bounds", () => {
    const backend = readBackendSpecs();

    expect(Object.keys(backend).sort()).toEqual([...PROCESSING_TUNING_KEYS].sort());
    for (const spec of Object.values(PROCESSING_TUNING_SPECS)) {
      expect([spec.defaultValue, spec.min, spec.max], spec.key).toEqual(
        backend[spec.key],
      );
    }
  });
});

describe("validateProcessingTuning", () => {
  it("accepts unset and in-range values", () => {
    expect(validateProcessingTuning({})).toBeNull();
    expect(validateProcessingTuning(processingTuningReset())).toBeNull();
    expect(
      validateProcessingTuning({ vad_threshold: 0.15, speaker_merge_threshold: 1 }),
    ).toBeNull();
  });

  it.each([
    [{ vad_threshold: 0.95 }],
    [{ speaker_merge_threshold: 0.29 }],
    [{ phantom_max_segments: 2.5 }],
    [{ asr_word_end_padding_s: Number.NaN }],
  ])("rejects %o", (settings) => {
    expect(validateProcessingTuning(settings)).not.toBeNull();
  });

  it("rejects a phantom floor at or above the default merge similarity", () => {
    expect(validateProcessingTuning({ phantom_embedding_floor: 0.65 })).toMatch(
      /floor/,
    );
    expect(
      validateProcessingTuning({
        phantom_embedding_floor: 0.65,
        phantom_merge_threshold: 0.8,
      }),
    ).toBeNull();
  });
});
