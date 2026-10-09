"use client";

import { FilePlus } from "lucide-react";

import { useCapture } from "@/lib/capture/CaptureProvider";
import { useImportAudioStore } from "@/lib/importAudioStore";

import Button, { type ButtonVariant } from "../ui/Button";
import IconButton from "../ui/IconButton";
import Tooltip from "../ui/Tooltip";

const LABEL = "Import Audio";
const LOCKED_HINT =
  "Resume or discard the paused recording before importing audio.";

interface ImportAudioButtonProps {
  /**
   * `icon` is the toolbar form: icon-only, with a tooltip as its visible
   * label. `labelled` is the empty-state form, with the label as text.
   */
  appearance?: "icon" | "labelled";
  /** Icon form only: which side the tooltip opens on. */
  tooltipPosition?: "top" | "bottom" | "left" | "right";
  /** Labelled form only. */
  variant?: ButtonVariant;
  /** Labelled form only. */
  className?: string;
}

/**
 * Opens the shared Import Audio dialog. A paused recording holds the capture
 * lock until it is resumed or discarded, so the button is disabled while it
 * does, and says why.
 */
export default function ImportAudioButton({
  appearance = "icon",
  tooltipPosition = "left",
  variant = "secondary",
  className,
}: ImportAudioButtonProps) {
  const { pausedRecording, runtimeActive } = useCapture();
  const openImportAudio = useImportAudioStore((state) => state.openImportAudio);
  const locked = Boolean(pausedRecording && !runtimeActive);

  if (appearance === "labelled") {
    return (
      <Button
        variant={variant}
        iconLeft={<FilePlus aria-hidden="true" className="h-4 w-4 shrink-0" />}
        onClick={openImportAudio}
        disabled={locked}
        title={locked ? LOCKED_HINT : undefined}
        className={className}
      >
        {LABEL}
      </Button>
    );
  }

  return (
    <Tooltip content={locked ? LOCKED_HINT : LABEL} position={tooltipPosition}>
      <IconButton
        aria-label={LABEL}
        size="sm"
        icon={<FilePlus aria-hidden="true" />}
        onClick={openImportAudio}
        disabled={locked}
        // Block-level inside the tooltip's inline-block wrapper, so the
        // wrapper takes the button's height rather than a text line's.
        className="flex text-rail-fg-muted hover:bg-rail-item-hover hover:text-rail-fg"
      />
    </Tooltip>
  );
}
