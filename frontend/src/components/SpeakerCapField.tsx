"use client";

import { ButtonBase, InputBase } from '@/components/ui/nativeBases';
import { InfoTip } from "@/components/ui/Tooltip";
import { Minus, Plus, Users } from "lucide-react";
import { useState } from "react";
import { useOnDepsChange } from "@/lib/useOnDepsChange";

export const MIN_SPEAKER_CAP = 1;
export const MAX_SPEAKER_CAP = 50;

/**
 * Where the first press of "+" lands when no cap is set.
 *
 * Not MIN_SPEAKER_CAP: a cap of one means "treat this whole meeting as a single
 * speaker", which is almost never what someone reaches for when they first
 * decide to bound the count. Typing 1 is still allowed.
 */
const FIRST_STEP_VALUE = 2;

interface SpeakerCapFieldProps {
  value: number | null;
  onCommit: (value: number | null) => void | Promise<void>;
  disabled?: boolean;
  size?: "compact" | "full";
  /**
   * "stacked" puts the label above a full-width control, which suits the
   * import and reprocess dialogs. "inline" collapses the whole thing to a row
   * sized to its content, for the live capture toolbar where a full-width
   * field spans the entire workspace for a two-character value. Either way
   * the hint is behind an "i" beside the box (#1000), and only a rejected
   * entry's message takes a line under it.
   */
  layout?: "stacked" | "inline";
  /**
   * Set when the field is shown during an active capture: the hint then says
   * why the live labels can show more speakers than the cap.
   */
  liveHint?: boolean;
  idPrefix?: string;
}

/**
 * Parse a raw field value into a usable cap.
 *
 * Returns `undefined` for input that is present but unusable, which the caller
 * treats as "do not commit yet" rather than "clear the cap" -- otherwise typing
 * over a value would briefly wipe it.
 */
export function parseSpeakerCap(raw: string): number | null | undefined {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  if (!/^\d+$/.test(trimmed)) return undefined;
  const parsed = Number.parseInt(trimmed, 10);
  if (!Number.isFinite(parsed)) return undefined;
  if (parsed < MIN_SPEAKER_CAP || parsed > MAX_SPEAKER_CAP) return undefined;
  return parsed;
}

/** Next value for a step, or `undefined` when the step is not available. */
export function steppedSpeakerCap(
  value: number | null,
  direction: 1 | -1,
): number | null | undefined {
  if (value == null) {
    return direction === 1 ? FIRST_STEP_VALUE : undefined;
  }
  const next = value + direction;
  if (next < MIN_SPEAKER_CAP) {
    // Stepping below the minimum returns to auto-detect rather than dead-ending,
    // so the control can undo itself without the user having to clear the text.
    return null;
  }
  if (next > MAX_SPEAKER_CAP) return undefined;
  return next;
}

export default function SpeakerCapField({
  value,
  onCommit,
  disabled = false,
  size = "full",
  layout = "stacked",
  liveHint = false,
  idPrefix = "speaker-cap",
}: SpeakerCapFieldProps) {
  const [draft, setDraft] = useState(value == null ? "" : String(value));
  const [invalid, setInvalid] = useState(false);
  // Only adopt an external value when the user is not mid-edit, so a poll that
  // refreshes the recording cannot yank the field out from under them.
  const [editing, setEditing] = useState(false);

  useOnDepsChange([value], () => {
    if (!editing) {
      setDraft(value == null ? "" : String(value));
    }
  });

  const commit = (raw: string) => {
    setEditing(false);
    const parsed = parseSpeakerCap(raw);
    if (parsed === undefined) {
      setInvalid(true);
      setDraft(value == null ? "" : String(value));
      return;
    }
    setInvalid(false);
    if (parsed !== value) {
      void onCommit(parsed);
    }
  };

  // Stepping has no blur to commit on, so it commits immediately.
  const step = (direction: 1 | -1) => {
    const next = steppedSpeakerCap(value, direction);
    if (next === undefined) return;
    setEditing(false);
    setInvalid(false);
    setDraft(next == null ? "" : String(next));
    void onCommit(next);
  };

  const inputId = `${idPrefix}-input`;
  const hintId = `${idPrefix}-hint`;
  const errorId = `${idPrefix}-error`;
  const inline = layout === "inline";
  const compact = size === "compact";

  const hintText = liveHint
    ? "Applied when you stop. Live labels are a preview and can show more speakers than this."
    : "Leave empty to auto-detect. Set this only if the meeting is split into more speakers than there were people.";
  const errorText = `Enter a whole number between ${MIN_SPEAKER_CAP} and ${MAX_SPEAKER_CAP}, or leave empty for auto-detect.`;

  const canDecrement = !disabled && steppedSpeakerCap(value, -1) !== undefined;
  const canIncrement = !disabled && steppedSpeakerCap(value, 1) !== undefined;

  const stepButtonClass =
    "flex shrink-0 items-center justify-center px-2.5 text-contrast-helper transition-colors hover:text-action-text disabled:cursor-not-allowed disabled:opacity-40";

  const stepper = (
    <div
      data-ui-fill="control"
      data-ui-border="full"
      data-ui="stepper"
      data-ui-field="custom"
      className={`flex items-stretch overflow-hidden rounded-xl border bg-control-bg transition-colors focus-within:border-action focus-within:ring-1 focus-within:ring-focus-ring ${
        compact ? "h-8" : "h-9"
        // Wide enough for the "Auto-detect" placeholder plus both step buttons;
        // 9.5rem clips the last letter.
      } ${inline ? "w-[11.5rem]" : "min-w-0 flex-1"} ${
        invalid
          ? "border-status-danger-border"
          : "border-control-border"
      } ${disabled ? "opacity-50" : ""}`}
    >
      <ButtonBase
        type="button"
        onClick={() => step(-1)}
        disabled={!canDecrement}
        className={stepButtonClass}
        aria-label="Decrease speaker limit"
        title="Decrease speaker limit"
      >
        <Minus className="h-3.5 w-3.5" />
      </ButtonBase>
      <InputBase
        id={inputId}
        type="number"
        inputMode="numeric"
        min={MIN_SPEAKER_CAP}
        max={MAX_SPEAKER_CAP}
        step={1}
        value={draft}
        disabled={disabled}
        placeholder="Auto-detect"
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? `${errorId} ${hintId}` : hintId}
        onFocus={() => {
          setEditing(true);
        }}
        onChange={(event) => {
          setEditing(true);
          setInvalid(false);
          setDraft(event.target.value);
        }}
        onBlur={(event) => commit(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.currentTarget.blur();
          }
          if (event.key === "Escape") {
            setEditing(false);
            setInvalid(false);
            setDraft(value == null ? "" : String(value));
            event.currentTarget.blur();
          }
        }}
        // The native spinner is unstyleable and looks foreign in both themes;
        // the flanking buttons replace it. Arrow keys still step the value.
        className="w-full min-w-0 border-0 bg-transparent px-1 text-center text-sm text-foreground outline-none placeholder:text-contrast-icon-muted disabled:cursor-not-allowed [appearance:textfield] [&::-webkit-inner-spin-button]:m-0 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:m-0 [&::-webkit-outer-spin-button]:appearance-none"
      />
      <ButtonBase
        type="button"
        onClick={() => step(1)}
        disabled={!canIncrement}
        className={stepButtonClass}
        aria-label="Increase speaker limit"
        title="Increase speaker limit"
      >
        <Plus className="h-3.5 w-3.5" />
      </ButtonBase>
    </div>
  );

  // The hint is the input's description whether or not its bubble shows.
  const field = (
    <div className="flex items-center gap-1">
      {stepper}
      <InfoTip id={hintId} label="About the speaker limit" content={hintText} />
    </div>
  );

  if (inline) {
    return (
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <label
          htmlFor={inputId}
          className="flex items-center gap-1.5 text-xs font-medium text-contrast-helper"
        >
          <Users className="h-3.5 w-3.5" aria-hidden="true" />
          Max Speakers
        </label>
        {/* The "i" wraps with the stepper, so a narrow toolbar puts the
            label on a line of its own rather than the icon (#538). */}
        {field}
        {/* An error to act on stays in view. Its own width does not count,
            so it wraps to the field's rather than widening the toolbar. */}
        {invalid ? (
          <p
            id={errorId}
            className="w-full text-right text-[11px] leading-snug text-status-danger-fg [contain:inline-size]"
          >
            {errorText}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div className={compact ? "space-y-1" : "space-y-1.5"}>
      <label
        htmlFor={inputId}
        className="flex items-center gap-1.5 text-xs font-medium text-contrast-helper"
      >
        <Users className="h-3.5 w-3.5" aria-hidden="true" />
        Max Speakers
        <span className="font-normal text-contrast-helper">
          (optional)
        </span>
      </label>
      {field}
      {invalid ? (
        <p id={errorId} className="text-[11px] leading-snug text-status-danger-fg">
          {errorText}
        </p>
      ) : null}
    </div>
  );
}
