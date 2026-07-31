"use client";

import { Info, Minus, Plus, Users } from "lucide-react";
import { useEffect, useRef, useState } from "react";

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
   * "stacked" puts the label above a full-width control with the hint beneath,
   * which suits the import and reprocess dialogs. "inline" collapses the whole
   * thing to a right-aligned row sized to its content, with the hint behind an
   * info affordance, for the live capture card where a full-width field spans
   * the entire workspace for a two-character value.
   */
  layout?: "stacked" | "inline";
  /** Set when the field is shown during an active capture. */
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
  const editing = useRef(false);

  useEffect(() => {
    if (!editing.current) {
      setDraft(value == null ? "" : String(value));
    }
  }, [value]);

  const commit = (raw: string) => {
    editing.current = false;
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
    editing.current = false;
    setInvalid(false);
    setDraft(next == null ? "" : String(next));
    void onCommit(next);
  };

  const inputId = `${idPrefix}-input`;
  const hintId = `${idPrefix}-hint`;
  const inline = layout === "inline";
  const compact = size === "compact";

  const hintText = invalid
    ? `Enter a whole number between ${MIN_SPEAKER_CAP} and ${MAX_SPEAKER_CAP}, or leave empty for auto-detect.`
    : liveHint
      ? "Leave empty to auto-detect. Applied when you stop, so you can change it if someone joins late."
      : "Leave empty to auto-detect. Set this only if the meeting is split into more speakers than there were people.";

  const canDecrement = !disabled && steppedSpeakerCap(value, -1) !== undefined;
  const canIncrement = !disabled && steppedSpeakerCap(value, 1) !== undefined;

  const stepButtonClass =
    "flex shrink-0 items-center justify-center px-2.5 text-contrast-helper transition-colors hover:text-action-text disabled:cursor-not-allowed disabled:opacity-40";

  const stepper = (
    <div
      className={`flex items-stretch overflow-hidden rounded-xl border bg-control-bg transition-colors focus-within:border-action focus-within:ring-1 focus-within:ring-focus-ring ${
        compact ? "h-8" : "h-9"
      } ${inline ? "w-[9.5rem]" : "w-full"} ${
        invalid
          ? "border-status-danger-border"
          : "border-control-border"
      } ${disabled ? "opacity-50" : ""}`}
    >
      <button
        type="button"
        onClick={() => step(-1)}
        disabled={!canDecrement}
        className={stepButtonClass}
        aria-label="Decrease speaker limit"
        title="Decrease speaker limit"
      >
        <Minus className="h-3.5 w-3.5" />
      </button>
      <input
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
        aria-describedby={hintId}
        onFocus={() => {
          editing.current = true;
        }}
        onChange={(event) => {
          editing.current = true;
          setInvalid(false);
          setDraft(event.target.value);
        }}
        onBlur={(event) => commit(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.currentTarget.blur();
          }
          if (event.key === "Escape") {
            editing.current = false;
            setInvalid(false);
            setDraft(value == null ? "" : String(value));
            event.currentTarget.blur();
          }
        }}
        // The native spinner is unstyleable and looks foreign in both themes;
        // the flanking buttons replace it. Arrow keys still step the value.
        className="w-full min-w-0 border-0 bg-transparent px-1 text-center text-sm text-foreground outline-none placeholder:text-contrast-icon-muted disabled:cursor-not-allowed [appearance:textfield] [&::-webkit-inner-spin-button]:m-0 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:m-0 [&::-webkit-outer-spin-button]:appearance-none"
      />
      <button
        type="button"
        onClick={() => step(1)}
        disabled={!canIncrement}
        className={stepButtonClass}
        aria-label="Increase speaker limit"
        title="Increase speaker limit"
      >
        <Plus className="h-3.5 w-3.5" />
      </button>
    </div>
  );

  if (inline) {
    return (
      <div className="flex flex-wrap items-center justify-end gap-x-2 gap-y-1">
        <label
          htmlFor={inputId}
          className="flex items-center gap-1.5 text-xs font-medium text-contrast-helper"
        >
          <Users className="h-3.5 w-3.5" aria-hidden="true" />
          Maximum speakers
        </label>
        {stepper}
        <span
          className="text-contrast-icon-muted"
          title={hintText}
          aria-hidden="true"
        >
          <Info className="h-3.5 w-3.5" />
        </span>
        {/* Present for screen readers and for the invalid case, where a tooltip
            alone would leave a rejected entry unexplained. */}
        <p
          id={hintId}
          className={
            invalid
              ? "w-full text-right text-[11px] leading-snug text-status-danger-fg"
              : "sr-only"
          }
        >
          {hintText}
        </p>
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
        Maximum speakers
        <span className="font-normal text-contrast-helper">
          (optional)
        </span>
      </label>
      {stepper}
      <p
        id={hintId}
        className="text-[11px] leading-snug text-contrast-helper"
      >
        {hintText}
      </p>
    </div>
  );
}
