"use client";

import { ChevronDown, Mic, Monitor } from "lucide-react";

import { useCapture } from "@/lib/capture/CaptureProvider";
import type { CaptureMode } from "@/lib/capture/shared";

import { SelectBase } from "./ui/nativeBases";

const MODE_LABELS: Record<CaptureMode, string> = {
  microphone_only: "Microphone only",
  shared_audio: "Microphone and a tab or screen",
};

/** The same compact native selector as note placement, beside every Start. */
export default function MeetingCaptureModeSelect({ disabled }: { disabled: boolean }) {
  const { settings, updateSettings } = useCapture();
  const mode = settings.startMode;
  const ModeIcon = mode === "microphone_only" ? Mic : Monitor;

  return (
    <div className={`relative h-10 w-10 shrink-0 ${disabled ? "opacity-50" : ""}`}>
      <SelectBase
        data-ui-fill="control"
        data-ui-disabled-fill="control-disabled"
        data-ui-border="full"
        aria-label="Recording source"
        title={`Recording source: ${MODE_LABELS[mode]}`}
        value={mode}
        disabled={disabled}
        onChange={(event) => updateSettings({ startMode: event.target.value as CaptureMode })}
        className="absolute inset-0 h-full w-full cursor-pointer appearance-none rounded-lg border border-control-border bg-control-bg text-transparent outline-none focus:border-action focus:ring-2 focus:ring-action disabled:cursor-not-allowed [&>option]:text-foreground"
      >
        {Object.entries(MODE_LABELS).map(([value, label]) => (
          <option key={value} value={value}>{label}</option>
        ))}
      </SelectBase>
      <span aria-hidden="true" className="pointer-events-none absolute inset-0 flex items-center justify-center text-contrast-muted">
        <ModeIcon className="h-4 w-4" />
        <ChevronDown className="h-3 w-3" />
      </span>
    </div>
  );
}
