import type { Settings } from "@/types";

/**
 * Shipped defaults and accepted ranges of the processing values a user may
 * override. Mirrors TUNING_SPECS in backend/processing/processing_tuning.py,
 * which is the authority: the API rejects anything outside these bounds.
 *
 * A null (or absent) value means inherit: the install's config.json value if
 * the operator set one, else the default below.
 */
export interface ProcessingTuningSpec {
  key: ProcessingTuningKey;
  label: string;
  defaultValue: number;
  min: number;
  max: number;
  step: number;
  integer?: boolean;
}

export type ProcessingTuningKey =
  | "vad_threshold"
  | "asr_word_end_padding_s"
  | "phantom_max_duration_s"
  | "phantom_max_segments"
  | "phantom_embedding_floor"
  | "phantom_merge_threshold"
  | "speaker_merge_threshold"
  | "word_flip_max_duration_s"
  | "word_flip_max_gap_s";

export const PROCESSING_TUNING_SPECS: Record<ProcessingTuningKey, ProcessingTuningSpec> = {
  vad_threshold: {
    key: "vad_threshold",
    label: "Speech detection threshold",
    defaultValue: 0.5,
    min: 0.15,
    max: 0.9,
    step: 0.05,
  },
  asr_word_end_padding_s: {
    key: "asr_word_end_padding_s",
    label: "Word end padding (seconds)",
    defaultValue: 0.2,
    min: 0.05,
    max: 0.8,
    step: 0.05,
  },
  phantom_max_duration_s: {
    key: "phantom_max_duration_s",
    label: "Max speech (seconds)",
    defaultValue: 3,
    min: 0,
    max: 10,
    step: 0.5,
  },
  phantom_max_segments: {
    key: "phantom_max_segments",
    label: "Max segments",
    defaultValue: 3,
    min: 0,
    max: 20,
    step: 1,
    integer: true,
  },
  phantom_embedding_floor: {
    key: "phantom_embedding_floor",
    label: "Non-speech floor",
    defaultValue: 0.35,
    min: 0,
    max: 0.95,
    step: 0.05,
  },
  phantom_merge_threshold: {
    key: "phantom_merge_threshold",
    label: "Merge similarity",
    defaultValue: 0.6,
    min: 0.05,
    max: 1,
    step: 0.05,
  },
  speaker_merge_threshold: {
    key: "speaker_merge_threshold",
    label: "Duplicate speaker merge similarity",
    defaultValue: 0.7,
    min: 0.3,
    max: 1,
    step: 0.01,
  },
  word_flip_max_duration_s: {
    key: "word_flip_max_duration_s",
    label: "Max word length (seconds)",
    defaultValue: 0.45,
    min: 0,
    max: 2,
    step: 0.05,
  },
  word_flip_max_gap_s: {
    key: "word_flip_max_gap_s",
    label: "Max gap (seconds)",
    defaultValue: 0.25,
    min: 0,
    max: 1,
    step: 0.05,
  },
};

export const PROCESSING_TUNING_KEYS = Object.keys(
  PROCESSING_TUNING_SPECS,
) as ProcessingTuningKey[];

/** Every tuning key set to null: back to inheriting the install's values. */
export function processingTuningReset(): Pick<Settings, ProcessingTuningKey> {
  return Object.fromEntries(
    PROCESSING_TUNING_KEYS.map((key) => [key, null]),
  ) as Pick<Settings, ProcessingTuningKey>;
}

function setValue(settings: Settings, key: ProcessingTuningKey): number | null {
  const value = settings[key];
  return typeof value === "number" ? value : null;
}

function effectiveValue(settings: Settings, key: ProcessingTuningKey): number {
  return setValue(settings, key) ?? PROCESSING_TUNING_SPECS[key].defaultValue;
}

/**
 * The first reason the tuning values would be rejected on save, or null.
 * Checked before autosave so a half-typed value shows an error instead of
 * sending a request the API refuses.
 */
export function validateProcessingTuning(settings: Settings): string | null {
  for (const spec of Object.values(PROCESSING_TUNING_SPECS)) {
    const value = settings[spec.key];
    if (value === null || value === undefined) {
      continue;
    }
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return `${spec.label} must be a number.`;
    }
    if (value < spec.min || value > spec.max) {
      return `${spec.label} must be between ${spec.min} and ${spec.max}.`;
    }
    if (spec.integer && !Number.isInteger(value)) {
      return `${spec.label} must be a whole number.`;
    }
  }

  if (
    effectiveValue(settings, "phantom_embedding_floor") >=
    effectiveValue(settings, "phantom_merge_threshold")
  ) {
    return "The phantom speaker non-speech floor must be lower than its merge similarity.";
  }
  return null;
}
