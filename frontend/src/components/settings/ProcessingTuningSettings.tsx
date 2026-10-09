"use client";

import { useId, useState, type ChangeEvent } from "react";
import { ArrowLeftRight, Ghost, Mic, RotateCcw, Timer } from "lucide-react";

import {
  PROCESSING_TUNING_SPECS,
  processingTuningReset,
  type ProcessingTuningKey,
} from "@/lib/processingTuning";
import type { Settings } from "@/types";

import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_BUTTON_SECONDARY, SETTINGS_INPUT_CLASS } from "./settingsControls";

interface ProcessingTuningSettingsProps {
  settings: Settings;
  onUpdate: (next: Settings) => void;
}

function formatValue(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

function readInput(event: ChangeEvent<HTMLInputElement>): number | null {
  // A half-typed number reads as "" with badInput set: report it as NaN so
  // validation shows an error, rather than as empty, which would reset it.
  if (event.target.validity.badInput) {
    return Number.NaN;
  }
  const text = event.target.value.trim();
  return text === "" ? null : Number(text);
}

interface TuningInputProps {
  tuningKey: ProcessingTuningKey;
  value: number | null | undefined;
  onChange: (key: ProcessingTuningKey, value: number | null) => void;
  /** Show the field's own label above it, for rows that hold several fields. */
  labelled?: boolean;
}

function TuningInput({ tuningKey, value, onChange, labelled = false }: TuningInputProps) {
  const spec = PROCESSING_TUNING_SPECS[tuningKey];
  const inputId = useId();
  const [draft, setDraft] = useState(formatValue(value));
  const [shownValue, setShownValue] = useState(value);

  // Follow a change made elsewhere (a reset, the initial load) without
  // rewriting what the user is typing when it already parses to that value.
  if (value !== shownValue) {
    setShownValue(value);
    const draftValue = draft.trim() === "" ? null : Number(draft);
    if (draftValue !== (value ?? null)) {
      setDraft(formatValue(value));
    }
  }

  return (
    <div className="min-w-0">
      {labelled && (
        <label htmlFor={inputId} className="mb-1 block text-xs font-medium contrast-helper">
          {spec.label}
        </label>
      )}
      <input
        id={inputId}
        type="number"
        inputMode={spec.integer ? "numeric" : "decimal"}
        min={spec.min}
        max={spec.max}
        step={spec.step}
        value={draft}
        placeholder={`Default (${spec.defaultValue})`}
        aria-label={labelled ? undefined : spec.label}
        onChange={(event) => {
          setDraft(event.target.value);
          onChange(tuningKey, readInput(event));
        }}
        className={SETTINGS_INPUT_CLASS}
      />
    </div>
  );
}

const ICON_CLASS = "h-4 w-4 contrast-icon-muted";

/**
 * Speech detection and speaker separation values a user may override for
 * their own recordings. Every field left empty inherits the installation's
 * value, or the shipped default its placeholder names.
 */
export default function ProcessingTuningSettings({
  settings,
  onUpdate,
}: ProcessingTuningSettingsProps) {
  const update = (key: ProcessingTuningKey, value: number | null) =>
    onUpdate({ ...settings, [key]: value });

  const input = (key: ProcessingTuningKey, labelled = false) => (
    <TuningInput
      tuningKey={key}
      value={settings[key]}
      onChange={update}
      labelled={labelled}
    />
  );

  return (
    <SettingsCard
      title="Speech and Speaker Tuning"
      description="Fine-tune how recordings are split into speech and speakers. Empty fields use the installation's value, or the default shown. Changes apply to recordings processed or reprocessed afterwards."
      headerAside={
        <button
          type="button"
          onClick={() => onUpdate({ ...settings, ...processingTuningReset() })}
          className={SETTINGS_BUTTON_SECONDARY}
        >
          <RotateCcw className="h-4 w-4" aria-hidden="true" />
          Reset to defaults
        </button>
      }
    >
      <SettingsRow
        id="recording-vad-threshold"
        label="Speech detection threshold"
        description="Lower keeps quiet or distant speech that would otherwise be muted before transcription; higher drops more background noise. Also used by the live transcript, even with voice activity detection off."
        icon={<Mic className={ICON_CLASS} aria-hidden="true" />}
      >
        {input("vad_threshold")}
      </SettingsRow>

      <SettingsRow
        id="recording-word-padding"
        label="Word end padding (Parakeet, Canary)"
        description="How long, in seconds, a word lasts when a pause follows it. Longer gives a short reply more chance to land on its speaker. Whisper ignores it."
        icon={<Timer className={ICON_CLASS} aria-hidden="true" />}
      >
        {input("asr_word_end_padding_s")}
      </SettingsRow>

      <SettingsRow
        id="recording-phantom-filter"
        label="Phantom speaker filter"
        description="A speaker under both limits is checked: below the floor it is treated as noise and reassigned, at or above the merge similarity it joins the closest speaker, and in between it is kept. Lower limits or a higher merge similarity keep more brief speakers. A limit of 0 turns the filter off."
        icon={<Ghost className={ICON_CLASS} aria-hidden="true" />}
      >
        <div className="grid grid-cols-2 gap-3">
          {input("phantom_max_duration_s", true)}
          {input("phantom_max_segments", true)}
          {input("phantom_embedding_floor", true)}
          {input("phantom_merge_threshold", true)}
        </div>
      </SettingsRow>

      <SettingsRow
        id="recording-word-flip"
        label="Single-word flip smoothing"
        description="A word up to this long, this close to its neighbours, is given back to the speaker on both sides of it. Lower values keep more one-word interjections; 0 turns smoothing off."
        icon={<ArrowLeftRight className={ICON_CLASS} aria-hidden="true" />}
      >
        <div className="grid grid-cols-2 gap-3">
          {input("word_flip_max_duration_s", true)}
          {input("word_flip_max_gap_s", true)}
        </div>
      </SettingsRow>
    </SettingsCard>
  );
}
