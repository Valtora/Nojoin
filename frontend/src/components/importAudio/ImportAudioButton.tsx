"use client";

import { FilePlus } from "lucide-react";

import { useCapture } from "@/lib/capture/CaptureProvider";
import { cn } from "@/lib/cn";
import { DEMO_MODE } from "@/lib/demo";
import { useImportAudioStore } from "@/lib/importAudioStore";

import Button from "../ui/Button";
import IconButton from "../ui/IconButton";

const LABEL = "Import Audio";

interface ImportAudioButtonProps {
  /**
   * `button` is the labelled secondary that sits beside Start Meeting and in
   * the empty list. `compact` is the phone list header's short form, and
   * `icon` the collapsed list strip's.
   */
  appearance?: "button" | "compact" | "icon";
  /** Layout classes from the caller, for the labelled forms. */
  className?: string;
}

/**
 * Opens the shared Import Audio dialog. Disabled while a paused recording
 * holds the capture lock, as Start Meeting is.
 */
export default function ImportAudioButton({
  appearance = "button",
  className,
}: ImportAudioButtonProps) {
  const { pausedRecording, runtimeActive } = useCapture();
  const openImportAudio = useImportAudioStore((state) => state.openImportAudio);
  const locked = Boolean(pausedRecording && !runtimeActive);
  // Imports are switched off on the public demo, like recording.
  const disabled = locked || DEMO_MODE;
  const title = DEMO_MODE ? "Importing is switched off in the demo." : LABEL;

  if (appearance === "icon") {
    return (
      <IconButton
        aria-label={LABEL}
        title={title}
        size="sm"
        icon={<FilePlus aria-hidden="true" />}
        onClick={openImportAudio}
        disabled={disabled}
        className="text-rail-fg-muted hover:bg-rail-item-hover hover:text-rail-fg"
      />
    );
  }

  return (
    <Button
      variant="secondary"
      iconLeft={<FilePlus aria-hidden="true" className="h-4 w-4 shrink-0" />}
      onClick={openImportAudio}
      disabled={disabled}
      title={DEMO_MODE ? title : undefined}
      // The short form keeps the full name for assistive tech.
      aria-label={appearance === "compact" ? LABEL : undefined}
      // Rounded and disabled like Start Meeting, which it sits beside.
      className={cn(
        "rounded-md disabled:border-transparent disabled:bg-action-tint disabled:text-action-tint-fg disabled:opacity-100",
        className,
      )}
    >
      {appearance === "compact" ? "Import" : LABEL}
    </Button>
  );
}
