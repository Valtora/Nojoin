import type { Settings } from "@/types";

import TUNING_BOUNDS from "./processingTuning.json";

/**
 * Shipped defaults and accepted ranges of the processing values a user may
 * override. The defaults and bounds come from processingTuning.json, which
 * mirrors TUNING_SPECS in backend/processing/processing_tuning.py, the
 * authority: the API rejects anything outside these bounds, and a backend
 * test fails when the JSON drifts from it. Labels and steps live here.
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
  integer: boolean;
}

export type ProcessingTuningKey =
  | "vad_threshold"
  | "asr_word_end_padding_s"
  | "phantom_max_duration_s"
  | "phantom_max_segments"
  | "phantom_embedding_floor"
  | "phantom_merge_threshold"
  | "word_flip_max_duration_s"
  | "word_flip_max_gap_s";

interface TuningBounds {
  default: number;
  min: number;
  max: number;
  integer: boolean;
}

// Typed as a full record, so a key missing from the JSON fails the build.
const BOUNDS: Record<ProcessingTuningKey, TuningBounds> = TUNING_BOUNDS;

function tuningSpec(key: ProcessingTuningKey, label: string, step: number): ProcessingTuningSpec {
  const bounds = BOUNDS[key];
  return {
    key,
    label,
    step,
    defaultValue: bounds.default,
    min: bounds.min,
    max: bounds.max,
    integer: bounds.integer,
  };
}

export const PROCESSING_TUNING_SPECS: Record<ProcessingTuningKey, ProcessingTuningSpec> = {
  vad_threshold: tuningSpec("vad_threshold", "Speech detection threshold", 0.05),
  asr_word_end_padding_s: tuningSpec("asr_word_end_padding_s", "Word end padding (seconds)", 0.05),
  phantom_max_duration_s: tuningSpec("phantom_max_duration_s", "Max speech (seconds)", 0.5),
  phantom_max_segments: tuningSpec("phantom_max_segments", "Max segments", 1),
  phantom_embedding_floor: tuningSpec("phantom_embedding_floor", "Non-speech floor", 0.05),
  phantom_merge_threshold: tuningSpec("phantom_merge_threshold", "Merge similarity", 0.05),
  word_flip_max_duration_s: tuningSpec("word_flip_max_duration_s", "Max word length (seconds)", 0.05),
  word_flip_max_gap_s: tuningSpec("word_flip_max_gap_s", "Max gap (seconds)", 0.05),
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

type PhantomPairKey = "phantom_embedding_floor" | "phantom_merge_threshold";

/**
 * The value processing will use, and where it comes from: the user's own, else
 * the installation's (sent read-only by the API), else the shipped default.
 */
function effectiveValue(
  settings: Settings,
  key: PhantomPairKey,
): { value: number; source: "user" | "installation" | "default" } {
  const own = settings[key];
  if (typeof own === "number") {
    return { value: own, source: "user" };
  }
  const installed = settings.phantom_thresholds_install?.[key];
  if (typeof installed === "number") {
    return { value: installed, source: "installation" };
  }
  return { value: PROCESSING_TUNING_SPECS[key].defaultValue, source: "default" };
}

function describe(effective: ReturnType<typeof effectiveValue>): string {
  return effective.source === "installation"
    ? `${effective.value}, the installation's value`
    : String(effective.value);
}

/** Why ``value`` is not usable for ``spec``, or null when it is. */
export function tuningValueError(spec: ProcessingTuningSpec, value: unknown): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return `${spec.label} must be a number.`;
  }
  if (value < spec.min || value > spec.max) {
    return `${spec.label} must be between ${spec.min} and ${spec.max}.`;
  }
  if (spec.integer && !Number.isInteger(value)) {
    return `${spec.label} must be a whole number.`;
  }
  return null;
}

/**
 * A field's text as a setting: null when empty (inherit), the number when it
 * is usable, and undefined when it is not (partial, out of range, a fraction
 * for a whole-number field).
 */
export function parseTuningInput(
  spec: ProcessingTuningSpec,
  text: string,
): number | null | undefined {
  const trimmed = text.trim();
  if (trimmed === "") {
    return null;
  }
  const value = Number(trimmed);
  return tuningValueError(spec, value) === null ? value : undefined;
}

function decimals(value: number): number {
  return (String(value).split(".")[1] ?? "").length;
}

/**
 * One step up or down from the shipped default, within bounds. An empty field
 * shows the default as its placeholder, so that is where stepping starts.
 */
export function stepFromDefault(spec: ProcessingTuningSpec, direction: 1 | -1): number {
  const stepped = spec.defaultValue + direction * spec.step;
  const clamped = Math.min(spec.max, Math.max(spec.min, stepped));
  // Rounded, so 0.7 + 0.05 reads 0.75 rather than 0.7499999999999999.
  return Number(clamped.toFixed(Math.max(decimals(spec.step), decimals(spec.defaultValue))));
}

/**
 * The first reason the tuning values would be rejected on save, or null.
 * Checked before autosave so an unusable value shows an error instead of
 * sending a request the API refuses.
 */
export function validateProcessingTuning(settings: Settings): string | null {
  for (const spec of Object.values(PROCESSING_TUNING_SPECS)) {
    const value = settings[spec.key];
    if (value === null || value === undefined) {
      continue;
    }
    const error = tuningValueError(spec, value);
    if (error) {
      return error;
    }
  }

  // Mirrors the API: the user must have set one of the pair for a conflict to
  // be theirs. One lying in the installation's values alone is ignored by
  // processing and must not block the page's saves.
  const floor = effectiveValue(settings, "phantom_embedding_floor");
  const merge = effectiveValue(settings, "phantom_merge_threshold");
  if ((floor.source === "user" || merge.source === "user") && floor.value >= merge.value) {
    return `The phantom speaker non-speech floor (${describe(floor)}) must be lower than its merge similarity (${describe(merge)}).`;
  }
  return null;
}
