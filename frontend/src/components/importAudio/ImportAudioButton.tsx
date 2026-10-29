"use client";

import { FilePlus } from "lucide-react";

import { useCapture } from "@/lib/capture/CaptureProvider";
import { cn } from "@/lib/cn";
import { DEMO_MODE } from "@/lib/demo";
import { getErrorMessage } from "@/lib/errors";
import { useImportAudioStore } from "@/lib/importAudioStore";
import { useNotificationStore } from "@/lib/notificationStore";

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
 * Requests unfinished-capture recovery before opening the shared Import Audio
 * dialog. Disabled while an offered recording awaits a decision.
 */
export default function ImportAudioButton({
  appearance = "button",
  className,
}: ImportAudioButtonProps) {
  const { pausedRecording, refreshPausedRecording, runtimeActive } = useCapture();
  const openImportAudio = useImportAudioStore((state) => state.openImportAudio);
  const addNotification = useNotificationStore((state) => state.addNotification);
  const locked = Boolean(pausedRecording && !runtimeActive);
  // Imports are switched off on the public demo, like recording.
  const disabled = locked || DEMO_MODE;
  const title = DEMO_MODE ? "Importing is switched off in the demo." : LABEL;

  const handleImport = async () => {
    try {
      if (!runtimeActive && await refreshPausedRecording({ onDemand: true })) {
        return;
      }
      openImportAudio();
    } catch (error: unknown) {
      addNotification({
        type: "error",
        message: getErrorMessage(error, "Failed to check for an unfinished recording. Try importing again."),
      });
    }
  };

  if (appearance === "icon") {
    return (
      <IconButton
        aria-label={LABEL}
        title={title}
        size="sm"
        icon={<FilePlus aria-hidden="true" />}
        onClick={handleImport}
        disabled={disabled}
        className="text-rail-fg-muted hover:bg-rail-item-hover hover:text-rail-fg"
      />
    );
  }

  return (
    <Button
      variant="secondary"
      iconLeft={<FilePlus aria-hidden="true" className="h-4 w-4 shrink-0" />}
      onClick={handleImport}
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
