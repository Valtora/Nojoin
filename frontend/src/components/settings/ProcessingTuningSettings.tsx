"use client";

import { ButtonBase, InputBase } from "@/components/ui/nativeBases";
import { Fragment, useId, useState } from "react";
import { RotateCcw } from "lucide-react";

import { cn } from "@/lib/cn";
import {
  PROCESSING_TUNING_SPECS,
  parseTuningInput,
  processingTuningReset,
  stepFromDefault,
  tuningValueError,
  type ProcessingTuningKey,
} from "@/lib/processingTuning";
import type { Settings } from "@/types";

import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_BUTTON_SECONDARY, SETTINGS_INPUT_CLASS } from "./settingsControls";
import { usesParakeet } from "./settingsRegistry";

interface ProcessingTuningSettingsProps {
  settings: Settings;
  onUpdate: (next: Settings) => void;
}

function formatValue(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

// As the checkboxes in SearchSettingsMenu.
const CHECKBOX_CLASS =
  "h-4 w-4 rounded border-control-border text-action-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring";

// The native spinner steps an empty field from its minimum rather than from
// the default shown, and looks foreign in both themes (as in SpeakerCapField).
// Arrow keys still step, through onKeyDown when the field is empty.
const HIDE_SPINNER_CLASS =
  "[appearance:textfield] [&::-webkit-inner-spin-button]:m-0 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:m-0 [&::-webkit-outer-spin-button]:appearance-none";

interface TuningInputProps {
  tuningKey: ProcessingTuningKey;
  value: number | null | undefined;
  onChange: (key: ProcessingTuningKey, value: number | null) => void;
  /** Show the field's own label above it, for rows that hold several fields. */
  labelled?: boolean;
  /** Id of the row's help text, announced with the field. */
  descriptionId: string;
}

export function TuningInput({
  tuningKey,
  value,
  onChange,
  labelled = false,
  descriptionId,
}: TuningInputProps) {
  const spec = PROCESSING_TUNING_SPECS[tuningKey];
  const inputId = useId();
  const errorId = useId();
  const [draft, setDraft] = useState(formatValue(value));
  // A number input reports text it cannot parse (Firefox accepts letters;
  // "-" or "e" mid-typing anywhere) as an empty value with badInput set.
  const [badInput, setBadInput] = useState(false);
  const [shownValue, setShownValue] = useState(value);

  // Follow a change made elsewhere (a reset, the initial load) without
  // rewriting what the user is typing when it already parses to that value.
  // Object.is, because a NaN from anywhere would otherwise never compare
  // equal and re-run this on every render.
  if (!Object.is(value, shownValue)) {
    setShownValue(value);
    const next = value ?? null;
    if (!Number.isNaN(next) && parseTuningInput(spec, draft) !== next) {
      setDraft(formatValue(next));
      setBadInput(false);
    }
  }

  const parsed = badInput ? undefined : parseTuningInput(spec, draft);
  const error =
    parsed === undefined
      ? (tuningValueError(spec, badInput ? Number.NaN : Number(draft)) ??
        `${spec.label} must be a number.`)
      : null;

  return (
    <div className="min-w-0">
      {labelled && (
        <label htmlFor={inputId} className="mb-2 block text-xs font-medium contrast-helper">
          {spec.label}
        </label>
      )}
      <InputBase
        data-ui-fill="control"
        data-ui-disabled-fill="control-disabled"
        data-ui-border="full"
        id={inputId}
        type="number"
        inputMode={spec.integer ? "numeric" : "decimal"}
        min={spec.min}
        max={spec.max}
        step={spec.step}
        value={draft}
        placeholder={`Default (${spec.defaultValue})`}
        aria-label={labelled ? undefined : spec.label}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${errorId} ${descriptionId}` : descriptionId}
        onChange={(event) => {
          const text = event.target.value;
          const bad = event.target.validity.badInput;
          setDraft(text);
          setBadInput(bad);
          // Only an empty field or a usable number reaches the settings. A
          // partial or out-of-range value stays in the field, marked invalid,
          // so it never blocks the autosave of every other setting.
          const next = bad ? undefined : parseTuningInput(spec, text);
          if (next !== undefined) {
            onChange(tuningKey, next);
          }
        }}
        onKeyDown={(event) => {
          if (draft !== "" || badInput) {
            return;
          }
          if (event.key !== "ArrowUp" && event.key !== "ArrowDown") {
            return;
          }
          // The browser steps an empty number input from 0 and clamps it to
          // the minimum, the far end of the range from the default shown.
          event.preventDefault();
          const next = stepFromDefault(spec, event.key === "ArrowUp" ? 1 : -1);
          setDraft(String(next));
          onChange(tuningKey, next);
        }}
        className={cn(SETTINGS_INPUT_CLASS, HIDE_SPINNER_CLASS, error && "border-danger-text")}
      />
      {error && (
        <p id={errorId} className="mt-1 text-xs text-danger-text">
          {error}
        </p>
      )}
    </div>
  );
}

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
  // Part of every field's key, so a reset remounts them: that is the only way
  // to clear text the browser could not parse, which reads as "" already.
  const [resetCount, setResetCount] = useState(0);

  // Row ids are unique registry ids, so ids derived from them are too.
  const labelId = (rowId: string) => `${rowId}-label`;
  const descriptionId = (rowId: string) => `${rowId}-description`;
  const groupId = (rowId: string) => `${rowId}-fields`;
  // Stored as an off switch, so an absent value keeps the filter running.
  const briefSpeakerCheckOn = settings.phantom_filter_off !== true;

  const input = (rowId: string, key: ProcessingTuningKey, labelled = false) => (
    <TuningInput
      key={`${key}-${resetCount}`}
      tuningKey={key}
      value={settings[key]}
      onChange={update}
      labelled={labelled}
      descriptionId={descriptionId(rowId)}
    />
  );

  // A row with several fields names them as one group, so "Merge similarity"
  // is announced as part of the phantom speaker filter.
  // A hidden group stays mounted, so its fields come back as they were.
  const group = (rowId: string, keys: ProcessingTuningKey[], hidden = false) => (
    <div
      role="group"
      id={groupId(rowId)}
      aria-labelledby={labelId(rowId)}
      hidden={hidden}
      className="grid grid-cols-2 gap-3"
    >
      {keys.map((key) => (
        <Fragment key={key}>{input(rowId, key, true)}</Fragment>
      ))}
    </div>
  );

  return (
    <SettingsCard
      title="Speech and speaker tuning"
      description="Fine-tune how recordings are split into speech and speakers. Empty fields use the installation's value, or the default shown; a saved value that is no longer accepted uses the default until this page saves again. Changes apply to recordings processed or reprocessed afterwards."
      headerAside={
        <ButtonBase
          data-ui-fill="card"
          data-ui-hover="inset"
          data-ui-active="inset"
          data-ui-border="full"
          chrome="action"
          type="button"
          onClick={() => {
            setResetCount((count) => count + 1);
            const reset = processingTuningReset();
            // The pause belongs to Transcription, outside this card's reset.
            delete reset.line_break_pause_s;
            onUpdate({ ...settings, ...reset, phantom_filter_off: null });
          }}
          className={SETTINGS_BUTTON_SECONDARY}
        >
          <RotateCcw className="h-4 w-4" aria-hidden="true" />
          Reset to defaults
        </ButtonBase>
      }
    >
      <SettingsRow
        id="recording-vad-threshold"
        labelId={labelId("recording-vad-threshold")}
        descriptionId={descriptionId("recording-vad-threshold")}
        label="Speech detection threshold"
        description="Lower keeps quiet or distant speech that would otherwise be muted before transcription; higher drops more background noise. Also used by the live transcript, even with voice activity detection off."
      >
        {input("recording-vad-threshold", "vad_threshold")}
      </SettingsRow>

      {/* Only Parakeet's word timings are capped, so other engines hide it. */}
      {usesParakeet(settings) && (
        <SettingsRow
          id="recording-word-length-cap"
          labelId={labelId("recording-word-length-cap")}
          descriptionId={descriptionId("recording-word-length-cap")}
          label="Longest word (Parakeet only)"
          description="How long, in seconds, one word can last; a word also ends where the next one starts. Raise it if one-word replies land on the wrong speaker; lower it if the last word of a sentence goes to whoever speaks next."
        >
          {input("recording-word-length-cap", "asr_word_length_cap_s")}
        </SettingsRow>
      )}

      <SettingsRow
        id="recording-phantom-filter"
        labelId={labelId("recording-phantom-filter")}
        descriptionId={descriptionId("recording-phantom-filter")}
        label="Speakers heard only briefly"
        // Unticking hides the fields, and a column sized by its content would
        // shrink to its minimum and hand the space to the label, moving the
        // checkbox. Held at the column's widest (max-w-sm), the row keeps the
        // layout the fields give it either way.
        controlClassName="@min-[26rem]:w-96"
        badge={
          <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs contrast-helper">
            <InputBase
              type="checkbox"
              checked={briefSpeakerCheckOn}
              aria-controls={groupId("recording-phantom-filter")}
              onChange={(event) =>
                onUpdate({
                  ...settings,
                  // Ticked clears the switch back to inherit, as a reset does.
                  phantom_filter_off: event.target.checked ? null : true,
                })
              }
              className={CHECKBOX_CLASS}
            />
            Check speakers heard only briefly
          </label>
        }
        description="Someone heard for less than both limits is compared with the other voices. A voice that matches no one below the noise limit is treated as noise and its words go to the nearest speaker; one that matches someone at or above the merge limit joins that person; anything between stays a separate speaker. Lower limits or a higher merge limit keep more brief speakers, and a limit of 0 keeps them all. Do that, or untick Check speakers heard only briefly to skip the check altogether, if a person who says only 'yes' or 'agreed' goes missing from the transcript."
      >
        {group("recording-phantom-filter", [
          "phantom_max_duration_s",
          "phantom_max_segments",
          "phantom_embedding_floor",
          "phantom_merge_threshold",
        ], !briefSpeakerCheckOn)}
      </SettingsRow>

      <SettingsRow
        id="recording-word-flip"
        labelId={labelId("recording-word-flip")}
        descriptionId={descriptionId("recording-word-flip")}
        label="Stray one-word speaker changes"
        description="A word up to this long, with no more than this pause either side, is given to the person who speaks before and after it. This removes stray one-word speaker changes but can erase a real interjection. Lower values keep more interjections; a word length of 0 keeps them all."
      >
        {group("recording-word-flip", ["word_flip_max_duration_s", "word_flip_max_gap_s"])}
      </SettingsRow>
    </SettingsCard>
  );
}
