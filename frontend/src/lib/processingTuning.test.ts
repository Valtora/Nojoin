import { describe, expect, it } from "vitest";

import { processingTuningReset, validateProcessingTuning } from "./processingTuning";

describe("validateProcessingTuning", () => {
  it("accepts unset and in-range values", () => {
    expect(validateProcessingTuning({})).toBeNull();
    expect(validateProcessingTuning(processingTuningReset())).toBeNull();
    expect(
      validateProcessingTuning({ vad_threshold: 0.15, phantom_merge_threshold: 1 }),
    ).toBeNull();
  });

  it.each([
    [{ vad_threshold: 0.95 }],
    [{ phantom_merge_threshold: 0.04 }],
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

  it("checks an empty half of the phantom pair against the installation's value", () => {
    const installFloor = {
      phantom_thresholds_install: {
        phantom_embedding_floor: 0.55,
        phantom_merge_threshold: null,
      },
    };
    expect(
      validateProcessingTuning({ ...installFloor, phantom_merge_threshold: 0.5 }),
    ).toBe(
      "The phantom speaker non-speech floor (0.55, the installation's value) must be lower than its merge similarity (0.5).",
    );

    // Above the shipped 0.6 merge similarity, but below the installation's 0.8.
    expect(
      validateProcessingTuning({
        phantom_embedding_floor: 0.7,
        phantom_thresholds_install: {
          phantom_embedding_floor: null,
          phantom_merge_threshold: 0.8,
        },
      }),
    ).toBeNull();
  });

  it("ignores a conflict in the installation's values alone", () => {
    expect(
      validateProcessingTuning({
        phantom_embedding_floor: null,
        phantom_merge_threshold: null,
        phantom_thresholds_install: {
          phantom_embedding_floor: 0.7,
          phantom_merge_threshold: 0.6,
        },
      }),
    ).toBeNull();
  });
});
