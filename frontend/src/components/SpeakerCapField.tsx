"use client";

import { Users } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export const MIN_SPEAKER_CAP = 1;
export const MAX_SPEAKER_CAP = 50;

interface SpeakerCapFieldProps {
  value: number | null;
  onCommit: (value: number | null) => void | Promise<void>;
  disabled?: boolean;
  size?: "compact" | "full";
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

export default function SpeakerCapField({
  value,
  onCommit,
  disabled = false,
  size = "full",
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

  const inputId = `${idPrefix}-input`;
  const compact = size === "compact";

  return (
    <div className={compact ? "space-y-1" : "space-y-1.5"}>
      <label
        htmlFor={inputId}
        className="flex items-center gap-1.5 text-xs font-medium text-gray-600 dark:text-gray-400"
      >
        <Users className="h-3.5 w-3.5" aria-hidden="true" />
        Maximum speakers
        <span className="font-normal text-gray-500 dark:text-gray-500">
          (optional)
        </span>
      </label>
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
        aria-describedby={`${idPrefix}-hint`}
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
        className={`w-full rounded-xl border bg-white px-3 text-sm text-gray-900 transition-colors placeholder:text-gray-400 focus:border-orange-400 focus:outline-none focus:ring-1 focus:ring-orange-400 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-gray-950/60 dark:text-gray-100 dark:placeholder:text-gray-500 ${
          compact ? "py-1.5" : "py-2"
        } ${
          invalid
            ? "border-red-400 dark:border-red-500/50"
            : "border-gray-300 dark:border-gray-700"
        }`}
      />
      <p
        id={`${idPrefix}-hint`}
        className="text-[11px] leading-snug text-gray-500 dark:text-gray-500"
      >
        {invalid
          ? `Enter a whole number between ${MIN_SPEAKER_CAP} and ${MAX_SPEAKER_CAP}, or leave empty for auto-detect.`
          : liveHint
            ? "Leave empty to auto-detect. Applied when you stop, so you can change it if someone joins late."
            : "Leave empty to auto-detect. Set this only if the meeting is split into more speakers than there were people."}
      </p>
    </div>
  );
}
