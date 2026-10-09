"use client";

import { FilePlus } from "lucide-react";

import { useCapture } from "@/lib/capture/CaptureProvider";
import { useImportAudioStore } from "@/lib/importAudioStore";

import Button from "../ui/Button";
import IconButton from "../ui/IconButton";

const LABEL = "Import Audio";

interface ImportAudioButtonProps {
  /**
   * `icon` is the toolbar form: icon-only, named by its title like the other
   * list header controls. `labelled` is the empty-state form.
   */
  appearance?: "icon" | "labelled";
}

/**
 * Opens the shared Import Audio dialog. Disabled while a paused recording
 * holds the capture lock, as the navigation entry it replaces was.
 */
export default function ImportAudioButton({
  appearance = "icon",
}: ImportAudioButtonProps) {
  const { pausedRecording, runtimeActive } = useCapture();
  const openImportAudio = useImportAudioStore((state) => state.openImportAudio);
  const locked = Boolean(pausedRecording && !runtimeActive);

  if (appearance === "labelled") {
    return (
      <Button
        iconLeft={<FilePlus aria-hidden="true" className="h-4 w-4 shrink-0" />}
        onClick={openImportAudio}
        disabled={locked}
      >
        {LABEL}
      </Button>
    );
  }

  return (
    <IconButton
      aria-label={LABEL}
      title={LABEL}
      size="sm"
      icon={<FilePlus aria-hidden="true" />}
      onClick={openImportAudio}
      disabled={locked}
      className="text-rail-fg-muted hover:bg-rail-item-hover hover:text-rail-fg"
    />
  );
}
