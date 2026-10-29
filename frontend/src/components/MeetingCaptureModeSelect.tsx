"use client";

import { ChevronDown, Mic, Monitor } from "lucide-react";
import type { ComponentPropsWithoutRef } from "react";

import { useCapture } from "@/lib/capture/CaptureProvider";
import type { CaptureMode } from "@/lib/capture/shared";

import { SETTINGS_SELECT_CLASS } from "./settings/settingsControls";
import { SelectBase } from "./ui/nativeBases";

const MODE_LABELS: Record<CaptureMode, string> = {
  microphone_only: "Microphone only",
  shared_audio: "Microphone and a tab or screen",
};

/**
 * The recording source chosen beside every Start. `labelled` shows the choice
 * as text where there is room (the dashboard); otherwise it is a compact icon
 * selector, the same as note placement.
 */
export default function MeetingCaptureModeSelect({
  disabled,
  labelled = false,
}: {
  disabled: boolean;
  labelled?: boolean;
}) {
  const { settings, updateSettings } = useCapture();
  const mode = settings.startMode;
  const ModeIcon = mode === "microphone_only" ? Mic : Monitor;
  const selectProps: ComponentPropsWithoutRef<"select"> = {
    "aria-label": "Recording source",
    title: `Recording source: ${MODE_LABELS[mode]}`,
    value: mode,
    disabled,
    onChange: (event) => updateSettings({ startMode: event.target.value as CaptureMode }),
  };
  const options = Object.entries(MODE_LABELS).map(([value, label]) => (
    <option key={value} value={value}>{label}</option>
  ));

  if (labelled) {
    return (
      <label className="flex items-center gap-2 text-sm text-contrast-muted">
        <span className="shrink-0">Recording source</span>
        <SelectBase
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
          {...selectProps}
          className={SETTINGS_SELECT_CLASS}
        >
          {options}
        </SelectBase>
      </label>
    );
  }

  return (
    <div className={`relative h-10 w-10 shrink-0 ${disabled ? "opacity-50" : ""}`}>
      <SelectBase
        data-ui-fill="control"
        data-ui-disabled-fill="control-disabled"
        data-ui-border="full"
        {...selectProps}
        className="absolute inset-0 h-full w-full cursor-pointer appearance-none rounded-lg border border-control-border bg-control-bg text-transparent outline-none focus:border-action focus:ring-2 focus:ring-action disabled:cursor-not-allowed [&>option]:text-foreground"
      >
        {options}
      </SelectBase>
      <span aria-hidden="true" className="pointer-events-none absolute inset-0 flex items-center justify-center text-contrast-muted">
        <ModeIcon className="h-4 w-4" />
        <ChevronDown className="h-3 w-3" />
      </span>
    </div>
  );
}
