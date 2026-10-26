"use client";

import { InputBase } from "@/components/ui/nativeBases";

interface ScreenStudyOptInProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  retentionDays: number;
  /** Frames looked at per second of audio: 1 or 2. */
  rateHz: number;
  /** The rail's narrow column gets the short wording. */
  compact?: boolean;
  disabled?: boolean;
  idPrefix: string;
}

/**
 * The screen study's opt-in at Start Meeting (#653). Shown only while the
 * installation allows the study and only to its primary Owner, and unticked
 * at the start of every meeting.
 */
export default function ScreenStudyOptIn({
  checked,
  onChange,
  retentionDays,
  rateHz,
  compact = false,
  disabled = false,
  idPrefix,
}: ScreenStudyOptInProps) {
  const id = `${idPrefix}-screen-study`;
  return (
    <label
      htmlFor={id}
      className={`flex items-start gap-2 ${disabled ? "cursor-not-allowed" : "cursor-pointer"}`}
    >
      <InputBase
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        aria-describedby={`${id}-help`}
        className="mt-0.5 h-4 w-4 shrink-0 accent-action"
      />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium text-foreground">Screen study</span>
        <span id={`${id}-help`} className="mt-0.5 block text-xs leading-5 text-contrast-helper">
          {compact
            ? `Samples the shared screen for the speaker study. Kept on this server, deleted after ${retentionDays} days.`
            : `Samples the shared screen about ${rateHz === 2 ? "twice a second" : "once a second"} while it changes, for the speaker study. The frames stay on this server, are never sent anywhere, and are deleted ${retentionDays} days after the meeting ends.`}
        </span>
      </span>
    </label>
  );
}
